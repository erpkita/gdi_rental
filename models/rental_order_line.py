# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError

class RentalOrderLine(models.Model):
    _name = 'gdi.rental.order.line'
    _description = 'Rental Order Line'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('gdi.rental.order', string='RO Reference', required=True,
                                   ondelete='cascade', index=True, copy=False)
    name = fields.Text(string='Description', required=True)
    sequence = fields.Integer(string='Sequence', default=10)

    product_id = fields.Many2one('product.product', string='Product', 
                                 domain="[('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
                                 change_default=True, ondelete='restrict')  # Unrequired company
    product_template_id = fields.Many2one(
        'product.template', string='Product Template',
        related="product_id.product_tmpl_id", domain=[('sale_ok', '=', True)])

    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom = fields.Many2one('uom.uom', 
                                  string='Unit of Measure', 
                                  domain="[('category_id', '=', product_uom_category_id)]", 
                                  ondelete="restrict")
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id')

    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', store=True)
    price_tax = fields.Float(compute='_compute_amount', string='Total Tax', store=True)
    price_total = fields.Monetary(compute='_compute_amount', string='Total', store=True)

    tax_id = fields.Many2many('account.tax', string='Taxes', context={'active_test': False})
    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)

    salesman_id = fields.Many2one(related='order_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(related='order_id.currency_id', depends=['order_id.currency_id'], store=True, string='Currency')
    company_id = fields.Many2one(related='order_id.company_id', string='Company', store=True, index=True)
    order_partner_id = fields.Many2one(related='order_id.partner_id', store=True, string='Customer', index=True)
    state = fields.Selection(
        related='order_id.state', string='Order Status', copy=False, store=True)
    
    available_qty = fields.Float(string="Available Qty", compute="_get_available_qty")
    src_location_id = fields.Many2one("stock.location", string="Source Location")
    available_src_location_ids = fields.Many2many("stock.location", string="Src Location Ids", compute="_get_available_src_location")
    available_src_location_txt = fields.Text("Available Src Location", compute="_get_available_src_location")    
    

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.order_id.currency_id, line.product_uom_qty, product=line.product_id, partner=line.order_id.partner_shipping_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    @api.depends('product_id', 'src_location_id')
    def _get_available_qty(self):
        for rec in self:
            rec.available_qty = 0
            if rec.product_id and rec.src_location_id:
                quant_query = """
                    SELECT SUM(quantity) AS total_qty FROM stock_quant
                    WHERE location_id = %s AND product_id = %s 
                """
                self._cr.execute(quant_query, (rec.src_location_id.id, rec.product_id.id, ))
                results = self._cr.dictfetchall()
                if results:
                    for stock in results:
                        rec.available_qty = stock['total_qty']
    
    @api.depends('product_id')
    def _get_available_src_location(self):
        for rec in self:
            if not rec.product_id:
                rec.available_src_location_txt = '-'
                rec.available_src_location_ids = False
            else:
                quant_query = """
                    SELECT loc.id AS location_id, SUM(quant.quantity) AS total_qty FROM stock_quant AS quant, stock_location AS loc
                    WHERE loc.usage = 'internal' AND
                          quant.location_id = loc.id AND
                          quant.quantity != 0.0 AND
                          quant.product_id = %s GROUP BY loc.id
                """
                self._cr.execute(quant_query, (rec.product_id.id, ))
                results = self._cr.dictfetchall()
                rec.available_src_location_txt = 'N/A'
                if len(results) > 0:
                    avail_stock_qty_txt = ''
                    location_ids = []
                    for stock in results:
                        location_id = self.env['stock.location'].browse(stock['location_id'])
                        location_ids.append(location_id.id)
                        availstock_location_text = '{} ({}) \n'.format(location_id.display_name, str(stock['total_qty']))
                        avail_stock_qty_txt += availstock_location_text
                    rec.available_src_location_txt = avail_stock_qty_txt
                    rec.available_src_location_ids = self.env['stock.location'].browse(location_ids)
                else:
                    rec.available_src_location_txt = 'N/A'
                    rec.available_src_location_ids = False