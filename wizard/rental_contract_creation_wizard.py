# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from dateutil.relativedelta import relativedelta
from odoo.exceptions import ValidationError

class RentalContractCreationWizard(models.TransientModel):
    _name = "rental.contract.creation.wizard"
    _description = "Rental Contract Creation Wizard"

    @api.model
    def default_get(self, fields_list):
        res = super(RentalContractCreationWizard, self).default_get(fields_list)

        if self._context.get('default_rental_id', False):
            rental_id = self.env["gdi.rental.order"].browse(self._context.get('default_rental_id'))
            if not rental_id:
                raise ValidationError(_("Invalid value on rental_id field. Please contact Administrator."))
            rental_item_ids = []
            for item in rental_id.order_line:
                rental_item_ids.append((
                    0, 0, {
                        'rental_order_line_id': item.id,
                        'price_unit': item.price_unit,
                        'product_uom_qty': item.product_uom_qty,
                        'duration': item.duration,
                        'product_uom': item.product_uom.id,
                        'product_uom_txt': item.product_uom_txt,
                        'duration_unit': item.duration_unit,
                        # 'start_date': item.start_date,
                        # 'end_date': item.end_date
                    }
                ))
            res.update({
                'duration': rental_id.duration,
                'duration_unit': rental_id.duration_unit,
                'rental_contract_wizard_ids': rental_item_ids
            })
        
        return res


    rental_id = fields.Many2one('gdi.rental.order', string="Rental No.")
    customer_reference = fields.Char(string="Customer Reference", required=True)
    customer_po_number = fields.Char(string="Customer PO. No.", required=True)
    start_date = fields.Date(string="Start Date", default=fields.Date.today())
    end_date = fields.Date(string="End Date", compute="_compute_end_date")

    duration = fields.Integer(string="Duration", 
                              required=True, 
                              default=1, 
                              compute="_compute_duration_from_lines",
                              inverse="_inverse_duration", store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], default='month', required=True,
    compute="_compute_duration_from_lines",
    inverse="_inverse_duration",
    store=True)

    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level')
    ], string="Date Definition Level", default='order', required=True,
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level.")
    rental_contract_wizard_ids = fields.One2many("rental.contract.wizard.line", "contract_wiz_id", string="Items")

    def _inverse_duration(self):
        pass
    
    @api.depends("rental_contract_wizard_ids", "rental_contract_wizard_ids.duration", "rental_contract_wizard_ids.duration_unit")
    def _compute_duration_from_lines(self):
        for record in self:
            record.update_header_duration()
    
    def update_header_duration(self):
        longest_days = 0
        longest_duration = self.duration
        longest_unit = self.duration_unit

        for line in self.rental_contract_wizard_ids:
            line_days = self._convert_to_days(line.duration, line.duration_unit)
            if line_days > longest_days:
                longest_days = line_days
                longest_duration = line.duration
                longest_unit = line.duration_unit
        
        self.duration = longest_duration
        self.duration_unit = longest_unit


    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        for record in self:
            if not record.start_date:
                record.end_date = False
                continue
                
            if record.duration_unit == 'hour':
                # For hours, we need to handle it differently as Date fields don't have hours
                # This is a simplified approach - you might need to convert to datetime if precision is critical
                record.end_date = record.start_date + relativedelta(hours=record.duration)
            elif record.duration_unit == 'day':
                record.end_date = record.start_date + relativedelta(days=record.duration)
            elif record.duration_unit == 'week':
                record.end_date = record.start_date + relativedelta(weeks=record.duration)
            elif record.duration_unit == 'month':
                record.end_date = record.start_date + relativedelta(months=record.duration)

    @api.model
    def _convert_to_days(self, duration, duration_unit):
        """Convert any duration unit to approximate days for comparison"""
        if duration_unit == 'hour':
            return duration / 24
        elif duration_unit == 'day':
            return duration
        elif duration_unit == 'week':
            return duration * 7
        elif duration_unit == 'month':
            return duration * 30  # Approximation
        return 0

    def action_create_contract(self):
        for rec in self:
            rental_id = rec.rental_id
            if rental_id:
                contract_id =  self.env["rental.contract"].create(rec._get_rental_contract_vals(rental_id))
                contract_id.write({
                    'date_definition_level': rec.date_definition_level
                })

                # apply contract line extended items.
                for line in rec.rental_contract_wizard_ids:
                    self.env["rental.contract.line"].create(
                        self._get_rental_contract_line_vals(line, contract_id)
                    )

                return rental_id.action_view_rental_contract(contract_id)
            
    def _get_rental_contract_vals(self, rental_id=None):
        if not rental_id:
            raise ValidationError(_("Validation error. Please contact your system administrator !"))
        
        rental = rental_id
        return {
            'partner_id': rental.partner_id.id or False,
            'pricelist_id': rental.pricelist_id.id or False,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': rental.user_id.id or False,
            'order_id': rental.id or False,
            'company_id': rental.company_id.id or False,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'currency_id': rental.currency_id.id or False,
            'contract_line_ids': [],
            'fiscal_position_id': rental.fiscal_position_id.id or False
        }

    def _get_rental_contract_line_vals(self, line, contract_id):
        if not line or not contract_id:
            raise ValidationError(_("Validation error. Please contact your system administrator !"))
        ro_line = line.rental_order_line_id

        contract_line_vals = {
            'ro_line_id': ro_line.id,
            'name': ro_line.name,
            'item_type': ro_line.item_type,
            'item_code': ro_line.item_code,
            'product_id': ro_line.product_id.id or False,
            'product_uom': ro_line.product_uom.id or False,
            'product_uom_qty': ro_line.product_uom_qty,
            'product_uom_txt': ro_line.product_uom_txt or "",
            'price_unit': line.price_unit,
            'tax_id' : ro_line.tax_id.ids or False,
            'duration': line.duration,
            # 'date_definition_level': line.date_definition_level or False,
            'start_date': line.start_date or False,
            'end_date': line.end_date or False,
            'duration_unit': line.duration_unit,
            'contract_id': contract_id.id
        }
        if ro_line.item_type == 'set':
            component_records = []
            for rec in ro_line.component_line_ids:
                component_records.append((0, 0, {
                    'product_id': rec.product_id.id or False,
                    'name': rec.name or False,
                    'price_unit': rec.price_unit or 0.0,
                    'product_uom_qty': rec.product_uom_qty or 0.0,
                    'product_uom': rec.product_uom.id
                }))
            
            contract_line_vals.update({'component_line_ids': component_records})
        
        return contract_line_vals


class RentalContractWizardLine(models.TransientModel):
    _name = "rental.contract.wizard.line"
    _description = "Rental Contract Wizard Line"

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (0.0) / 100.0)
            taxes = line.rental_order_line_id.tax_id.compute_all(price, line.contract_wiz_id.rental_id.currency_id, line.product_uom_qty, product=line.product_id, partner=line.contract_wiz_id.rental_id.partner_shipping_id)
            line.update({
                'price_tax': sum(t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    contract_wiz_id = fields.Many2one("rental.contract.creation.wizard", string="Header Ref.")
    rental_order_line_id = fields.Many2one("gdi.rental.order.line", string="Rental Order Line Ref.")
    product_id = fields.Many2one("product.product", related="rental_order_line_id.product_id", string="Product")
    currency_id = fields.Many2one("res.currency", related="rental_order_line_id.currency_id", string="Currency")
    name = fields.Text(string="Description", related="rental_order_line_id.name")
    item_type = fields.Selection(related="rental_order_line_id.item_type", string="Item Type", required=True)
    item_code = fields.Char(related="rental_order_line_id.item_code", string="Item Code")
    start_date = fields.Date(string="Start Date", related="contract_wiz_id.start_date")
    end_date = fields.Date(string="End Date", compute="_compute_end_date")
    duration = fields.Integer(string="Duration")
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months')
    ], default='month', string="Duration Unit", required=True)
    duration_string = fields.Char(string="Duration String", compute="_compute_duration_string")
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

    @api.depends("duration", "duration_unit")
    def _compute_duration_string(self):
        for rec in self:
            rec.duration_string = f"{rec.duration} {dict(self._fields['duration_unit'].selection).get(rec.duration_unit, 'Not Defined')}"


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