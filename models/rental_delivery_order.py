# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

class StockPicking(models.Model):
    _inherit = "stock.picking"
    _order = "scheduled_date desc, id desc"

    rental_order_item_ids = fields.One2many("stock.rental.order.item", "picking_id", string="Rental Order Items")
    gdi_rental_id = fields.Many2one("gdi.rental.order", string="Rental")
    rental_contract_id = fields.Many2one("rental.contract", string="Contract")
    is_rental_do = fields.Boolean(string="RDO ?", default=False)

    @api.onchange('is_rental_do')
    def onchange_rental_do(self):
        default_picking_type = "Delivery Orders"
        rental_picking_type = self.env["stock.picking.type"].search([('name', '=', "Rental Delivery Orders")], limit=1)
        if not rental_picking_type:
            rental_picking_type = self.env["stock.picking.type"].search([('name', '=', default_picking_type)], limit=1)
        for rec in self:
            rec.picking_type_id = rental_picking_type.id

class StockRentalOrderItem(models.Model):
    _name = "stock.rental.order.item"

    picking_id = fields.Many2one("stock.picking", string="Picking Reference.", required=True,
                                 ondelete="cascade", index=True, copy=False)
    name = fields.Text(string='Description', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    item_code = fields.Char(string="Item Code", required=True)
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
    product_uom_txt = fields.Char(string="Uom", default="")

    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    price_subtotal = fields.Monetary(compute='_compute_amount', string='Subtotal', store=True)
    price_tax = fields.Float(compute='_compute_amount', string='Total Tax', store=True)
    price_total = fields.Monetary(compute='_compute_amount', string='Total', store=True)

    tax_id = fields.Many2many('account.tax', string='Taxes', context={'active_test': False})
    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)

    salesman_id = fields.Many2one(related='picking_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(related='picking_id.currency_id', depends=['picking_id.currency_id'], store=True, string='Currency')
    company_id = fields.Many2one(related='picking_id.company_id', string='Company', store=True, index=True)
    order_partner_id = fields.Many2one(related='picking_id.partner_id', store=True, string='Customer', index=True)
    
    item_type = fields.Selection([('unit', 'Unit'), ('set', 'Set')], default='unit', string="Type", required=True)

    start_date = fields.Date(string="Start Date", required=False)
    end_date = fields.Date(string="End Date", required=False)

    duration = fields.Integer(string="Duration", default=1, required=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", default='day', required=True)

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.picking_id.currency_id, line.product_uom_qty, product=line.product_id, partner=line.picking_id.partner_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })
  