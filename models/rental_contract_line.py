# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools.misc import get_lang
from dateutil.relativedelta import relativedelta

class RentalContractLine(models.Model):
    _name = 'rental.contract.line'
    _description = 'Rental Contract Line'
    _order = 'contract_id, sequence, id'

    contract_id = fields.Many2one('rental.contract', string='Contract Reference', required=True,
                                   ondelete='cascade', index=True, copy=False)
    ro_line_id = fields.Many2one('gdi.rental.order.line', string='RO Line Ref#',
                                   ondelete='cascade', index=True, copy=False)
    name = fields.Text(string='Description', required=True)
    sequence = fields.Integer(string='Sequence', default=10)
    item_code = fields.Char(string="Item Code", related="ro_line_id.item_code", required=True)
    product_id = fields.Many2one('product.product', string='Product', 
                                 domain="[('sale_ok', '=', True), '|', ('company_id', '=', False), ('company_id', '=', company_id)]",
                                 change_default=True, ondelete='restrict')
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
    company_id = fields.Many2one(related='contract_id.company_id', string='Company', store=True, index=True)
    currency_id = fields.Many2one(related='contract_id.currency_id', depends=['contract_id.currency_id'], store=True, string='Currency')
    order_partner_id = fields.Many2one(related='contract_id.partner_id', store=True, string='Customer', index=True)
    
    item_type = fields.Selection([('unit', 'Unit'), ('set', 'Set')], related="ro_line_id.item_type", default='unit', string="Type", required=True)
    start_date = fields.Date(string="Start Date", required=False)
    end_date = fields.Date(string="End Date", compute='_compute_end_date', required=False)

    duration = fields.Integer(string="Duration", required=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", required=True)

    duration_string = fields.Char(string="Duration Str", compute='_compute_duration_string')

    discount = fields.Float(string='Discount (%)', digits='Discount', default=0.0)
    date_definition_level = fields.Selection(
        related="contract_id.date_definition_level", string="Date Definition Level",
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level."
    )

    component_line_ids = fields.One2many("rental.contract.component", 
                                         "contract_line_id", 
                                         string="Components")
    
    @api.model
    def default_get(self, fields_list):
        res = super(RentalContractLine, self).default_get(fields_list)

        if self._context.get("default_contract_id"):
            contract_id = self._context.get("default_contract_id")
            order = self.env["rental.contract"].browse(contract_id)
            if order:
                res.update({
                    "duration": order.duration,
                    "duration_unit": order.duration_unit,
                    "start_date": order.start_date
                })
        
        return res
    
    @api.depends('duration', 'duration_unit')
    def _compute_duration_string(self):
        for record in self:
            record.duration_string = f"{record.duration} {dict(self._fields['duration_unit'].selection).get(record.duration_unit)}"
    
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
    
    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(price, line.contract_id.currency_id, line.product_uom_qty, product=line.product_id, partner=line.contract_id.partner_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })
    
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
        lang = get_lang(self.env, self.contract_id.partner_id.lang).code
        product = self.product_id.with_context(
            lang=lang,
        )

        self.update({'name': product.display_name,
                     'item_code': product.item_code_ref})

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
            partner=self.contract_id.partner_id,
            quantity=vals.get('product_uom_qty') or self.product_uom_qty,
            date=self.contract_id.order_id.date_order,
            pricelist=self.contract_id.pricelist_id.id,
            uom=self.product_uom.id
        )

        if self.contract_id.pricelist_id and self.contract_id.partner_id:
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
                self.contract_id.currency_id,
                self.contract_id.order_id.date_order,
                'sale',
                fiscal_position=self.contract_id.fiscal_position_id,
                product_price_unit=rental_price,
                product_currency=self.contract_id.currency_id
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