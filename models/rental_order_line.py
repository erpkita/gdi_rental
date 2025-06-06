# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import get_lang

from dateutil.relativedelta import relativedelta

class GDIRentalOrderLine(models.Model):
    _name = 'gdi.rental.order.line'
    _description = 'Rental Order Line'
    _order = 'order_id, sequence, id'

    order_id = fields.Many2one('gdi.rental.order', string='RO Reference', required=True,
                                   ondelete='cascade', index=True, copy=False)
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

    salesman_id = fields.Many2one(related='order_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(related='order_id.currency_id', depends=['order_id.currency_id'], store=True, string='Currency')
    company_id = fields.Many2one(related='order_id.company_id', string='Company', store=True, index=True)
    order_partner_id = fields.Many2one(related='order_id.partner_id', store=True, string='Customer', index=True)
    state = fields.Selection(
        related='order_id.state', string='Order Status', copy=False, store=True)
    
    item_type = fields.Selection([('unit', 'Unit'), ('set', 'Set')], default='unit', string="Type", required=True)
    date_definition_level = fields.Selection(
        related="order_id.date_definition_level", string="Date Definition Level",
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level."
    )

    duration = fields.Integer(string="Duration", required=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", required=True)

    available_qty = fields.Float(string="Available Qty", compute="_get_available_qty")
    src_location_id = fields.Many2one("stock.location", string="Source Location")
    available_src_location_ids = fields.Many2many("stock.location", string="Src Location Ids", compute="_get_available_src_location")
    available_src_location_txt = fields.Text("Available Src Location", compute="_get_available_src_location")    
    
    component_line_ids = fields.One2many("rental.order.component", 
                                         "order_line_id", 
                                         string="Components")
    
    start_date = fields.Date(string="Start Date", compute='_compute_start_date', store=True, inverse='_inverse_start_date')
    end_date = fields.Date(string="End Date", compute='_compute_end_date', store=True, inverse='_inverse_end_date')

    duration_string = fields.Char(string="Duration Str", compute="_compute_duration_str")

    rental_state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('hireoff', 'Hired-Off')
    ], string="Rental Status", default="draft")

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        for record in self:
            duration_unit = dict(self._fields['duration_unit'].selection).get(record.duration_unit, 'Not Defined')
            record.duration_string = f"{record.duration} {duration_unit}"

    @api.model
    def default_get(self, fields_list):
        res = super(GDIRentalOrderLine, self).default_get(fields_list)

        if self._context.get("default_order_id"):
            order_id = self._context.get("default_order_id")
            order = self.env["gdi.rental.order"].browse(order_id)
            if order:
                res.update({
                    "duration": order.duration,
                    "duration_unit": order.duration_unit,
                    "start_date": order.start_date
                })
        
        return res
    
    @api.depends('order_id', 'order_id.start_date')
    def _compute_start_date(self):
        for record in self:
            start_date = record.order_id.start_date
            record.start_date = start_date
    
    def _inverse_start_date(self):
        pass
    
    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        for record in self:
            if not record.start_date:
                record.end_date = False
                continue
                
            if record.duration_unit == 'hour':
                record.end_date = record.start_date + relativedelta(hours=record.duration)
            elif record.duration_unit == 'day':
                record.end_date = record.start_date + relativedelta(days=record.duration)
            elif record.duration_unit == 'week':
                record.end_date = record.start_date + relativedelta(weeks=record.duration)
            elif record.duration_unit == 'month':
                record.end_date = record.start_date + relativedelta(months=record.duration)

    def _inverse_end_date(self):
        pass

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

    @api.onchange('item_type')
    def onchange_item_type(self):
        for rec in self:
            rec.product_id = False
            rec.product_uom_txt = 'SET'

    @api.onchange('product_id')
    def product_id_change(self):
        self._update_description()
        self._update_taxes()

    @api.onchange('duration_unit', 'duration')
    def onchange_duration(self):
        self._update_taxes()
    
    def _update_description(self):
        if not self.product_id:
            self.name = False
        lang = get_lang(self.env, self.order_id.partner_id.lang).code
        product = self.product_id.with_context(
            lang=lang,
        )

        self.update({'name': product.display_name})

    def _update_taxes(self):
        vals = {}
        if not self.product_id:
            vals['product_uom'] = False
            vals['product_uom_qty'] = 1.0
            vals['price_unit'] = 0.0
            vals['product_uom_txt'] = "SET"
            self.update(vals)
            return
        
        if not self.product_uom or (self.product_id.uom_id.id != self.product_uom.id):
            vals['product_uom'] = self.product_id.uom_id
            vals['product_uom_qty'] = self.product_uom_qty or 1.0
            vals['product_uom_txt'] = self.product_id.uom_id.name

        product = self.product_id.with_context(
            partner=self.order_id.partner_id,
            quantity=vals.get('product_uom_qty') or self.product_uom_qty,
            date=self.order_id.date_order,
            pricelist=self.order_id.pricelist_id.id,
            uom=self.product_uom.id
        )

        if self.order_id.pricelist_id and self.order_id.partner_id:
            rental_pricing_list = self._get_rental_pricing_list(product)
            if not rental_pricing_list:
                raise ValidationError(
                    "Rental price for the selected duration (%s) is not configured for this product. Please contact the administrator or choose different duration." % (self.duration_unit)
                )
            rental_pricing_keys = rental_pricing_list.keys()
            if self.duration_unit not in rental_pricing_keys:
                readable_units = ", ".join(rental_pricing_keys)
                raise ValidationError(
                    f"This product is not available for rental by {self.duration_unit}. "
                    f"Please choose from the available options: {readable_units}."
                )

            rental_price = rental_pricing_list[self.duration_unit] * self.duration
            vals['price_unit'] = product._get_tax_included_unit_price(
                self.company_id,
                self.order_id.currency_id,
                self.order_id.date_order,
                'sale',
                fiscal_position=self.order_id.fiscal_position_id,
                product_price_unit=rental_price,
                product_currency=self.order_id.currency_id
            )

        self.update(vals)

    def _get_rental_pricing_list(self, product):
        if not product or not product.rental_pricing_ids:
            return False
        
        return {price.unit: price.price for price in product.rental_pricing_ids}
    
    @api.onchange('component_line_ids')
    def onchange_component_line_ids(self):
        for rec in self:
            total_price_unit = 0.0
            for comp in rec.component_line_ids:
                total_price_unit += comp.price_subtotal
            rec.update({
                'price_unit': total_price_unit
            })

    def check_rental_period(self):
        for rec in self:
            if not rec.start_date:
                raise ValidationError(_(f"Rental period start date for item code {rec.item_code} is not defined. Please define it before starting the rental."))
            if not rec.end_date:
                raise ValidationError(_(f"Rental period end date for item code {rec.item_code} is not defined. Please define it before starting the rental."))

    def _get_contract_line_vals(self):
        for rec in self:
            contract_line_vals =  {
                'name': rec.name or '',
                'item_code': rec.item_code or '',
                'sequence': rec.sequence or '',
                'product_id': rec.product_id.id or False,
                'product_template_id': rec.product_template_id.id or False,
                'product_uom_qty': rec.product_uom_qty or 1.0,
                'product_uom': rec.product_uom.id or False,
                'product_uom_category_id': rec.product_uom_category_id.id or False,
                'product_uom_txt': rec.product_uom_txt or 'SET',
                'price_unit': rec.price_unit or 0.0,
                'item_type': rec.item_type,
                'start_date': rec.start_date or False,
                'end_date': rec.end_date or False,
                'duration': rec.duration or False,
                'duration_unit': rec.duration_unit or False,
                'ro_line_id': rec.id or False
            }
            if rec.item_type == 'set':
                component_line_ids = []
                for comp in rec.component_line_ids:
                    component_line_ids.append((
                        0, 0, {
                            'product_id': comp.product_id.id or False,
                            'name': comp.name or False,
                            'price_unit': comp.price_unit or 0.0,
                            'product_uom_qty': comp.product_uom_qty or 0.0,
                            'product_uom': comp.product_uom.id
                        }
                    ))
                contract_line_vals.update({'component_line_ids': component_line_ids})

            return contract_line_vals
        
    def action_item_hireoff(self):
        pass




