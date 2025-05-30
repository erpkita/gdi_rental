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
                        'duration_unit': item.duration_unit,
                        'start_date': item.start_date,
                        'end_date': item.end_date
                    }
                ))
            res.update({
                'rental_contract_wizard_ids': rental_item_ids
            })
        
        return res


    rental_id = fields.Many2one('gdi.rental.order', string="Rental No.")
    customer_reference = fields.Char(string="Customer Reference", required=True)
    customer_po_number = fields.Char(string="Customer PO. No.", required=True)
    start_date = fields.Date(string="Start Date", default=fields.Date.today())
    end_date = fields.Date(string="End Date", compute="_compute_end_date")
    duration = fields.Integer(string="Duration", required=True, default=1)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], default='month', required=True)
    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level')
    ], string="Date Definition Level", default='order', required=True,
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level.")
    rental_contract_wizard_ids = fields.One2many("rental.contract.wizard.line", "contract_wiz_id", string="Items")

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

    def action_create_contract(self):
        for rec in self:
            rental_id = rec.rental_id
            if rental_id:
                contract_id = rental_id.action_generate_contract()
                contract_id.write({
                    'date_definition_level': rec.date_definition_level
                })

                return rental_id.action_view_rental_contract(contract_id)
            

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
    start_date = fields.Date(string="Start Date")
    end_date = fields.Date(string="End Date")
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

