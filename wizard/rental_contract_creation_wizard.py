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
            rental_id = self.env["gdi.rental.order"].browse(
                self._context.get('default_rental_id'))
            if not rental_id:
                raise ValidationError(
                    _("Invalid value on rental_id field. "
                      "Please contact Administrator."))
            rental_item_ids = []
            rental_orderlines = rental_id.order_line.filtered(
                lambda line: line.rental_state == 'active')
            for item in rental_orderlines:
                rental_item_ids.append((
                    0, 0, {
                        'rental_order_line_id': item.id,
                        'price_unit': item.price_unit,
                        'product_uom_qty': item.product_uom_qty,
                        'product_uom': item.product_uom.id,
                        'product_uom_txt': item.product_uom_txt,
                        'start_date': item.start_date,
                        'end_date': item.end_date,
                    }
                ))
            res.update({
                'start_date': rental_id.start_date,
                'end_date': rental_id.end_date,
                'rental_contract_wizard_ids': rental_item_ids,
            })

        return res

    rental_id = fields.Many2one(
        'gdi.rental.order', string="Rental No.")
    customer_reference = fields.Char(
        string="Customer Reference", required=True)
    customer_po_number = fields.Char(
        string="Customer PO. No.", required=True)

    # --- Dates & Duration ---
    start_date = fields.Date(
        string="Start Date", default=fields.Date.today())
    end_date = fields.Date(string="End Date")
    rental_duration = fields.Float(
        string='Duration (Months)',
        compute='_compute_rental_duration', store=True, readonly=False)

    # Backward compatibility
    duration = fields.Integer(
        string='Duration',
        compute='_compute_duration_compat', store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], default='month', string="Unit")

    rental_contract_wizard_ids = fields.One2many(
        "rental.contract.wizard.line", "contract_wiz_id", string="Items")

    @api.depends('start_date', 'end_date')
    def _compute_rental_duration(self):
        for rec in self:
            if rec.start_date and rec.end_date:
                delta = relativedelta(rec.end_date, rec.start_date)
                rec.rental_duration = (
                    (delta.years * 12) + delta.months + (delta.days / 30.0))
            else:
                rec.rental_duration = 0.0

    @api.depends('rental_duration')
    def _compute_duration_compat(self):
        for rec in self:
            rec.duration = max(1, round(rec.rental_duration or 1))

    def action_create_contract(self):
        for rec in self:
            rental_id = rec.rental_id
            if rental_id:
                # Find the latest existing contract (will be closed after
                # the new contract is fully created)
                prev_contract_ids = rental_id.rental_contract_ids.sorted(
                    key=lambda r: r.id, reverse=True)
                lastest_contract = prev_contract_ids[:1]

                # Create new contract with link to previous
                contract_vals = rec._get_rental_contract_vals(rental_id)
                if lastest_contract:
                    contract_vals['previous_contract_id'] = lastest_contract.id

                contract_id = self.env["rental.contract"].create(contract_vals)

                # Create contract line items
                for line in rec.rental_contract_wizard_ids:
                    self.env["rental.contract.line"].create(
                        self._get_rental_contract_line_vals(line, contract_id))

                # Close previous contract only AFTER new one is fully created
                if lastest_contract:
                    lastest_contract.write({'state': 'done'})

                return rental_id.action_view_rental_contract(contract_id)

    def _get_rental_contract_vals(self, rental_id=None):
        if not rental_id:
            raise ValidationError(
                _("Validation error. "
                  "Please contact your system administrator!"))

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
            'fiscal_position_id': rental.fiscal_position_id.id or False,
            'duration_unit': 'month',
        }

    def _get_rental_contract_line_vals(self, line, contract_id):
        if not line or not contract_id:
            raise ValidationError(
                _("Validation error. "
                  "Please contact your system administrator!"))
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
            'tax_id': ro_line.tax_id.ids or False,
            'start_date': line.start_date or False,
            'end_date': line.end_date or False,
            'contract_id': contract_id.id,
        }
        if ro_line.item_type == 'set':
            component_records = []
            for rec in ro_line.component_line_ids:
                component_records.append((0, 0, {
                    'product_id': rec.product_id.id or False,
                    'name': rec.name or False,
                    'price_unit': rec.price_unit or 0.0,
                    'product_uom_qty': rec.product_uom_qty or 0.0,
                    'product_uom': rec.product_uom.id,
                }))
            contract_line_vals.update({
                'component_line_ids': component_records,
            })

        return contract_line_vals


class RentalContractWizardLine(models.TransientModel):
    _name = "rental.contract.wizard.line"
    _description = "Rental Contract Wizard Line"

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (0.0) / 100.0)
            taxes = line.rental_order_line_id.tax_id.compute_all(
                price,
                line.contract_wiz_id.rental_id.currency_id,
                line.product_uom_qty,
                product=line.product_id,
                partner=line.contract_wiz_id.rental_id.partner_shipping_id)
            line.update({
                'price_tax': sum(
                    t.get('amount', 0.0) for t in taxes.get('taxes', [])),
                'price_total': taxes['total_included'],
                'price_subtotal': taxes['total_excluded'],
            })

    contract_wiz_id = fields.Many2one(
        "rental.contract.creation.wizard", string="Header Ref.")
    rental_order_line_id = fields.Many2one(
        "gdi.rental.order.line", string="Rental Order Line Ref.")
    product_id = fields.Many2one(
        "product.product",
        related="rental_order_line_id.product_id", string="Product")
    currency_id = fields.Many2one(
        "res.currency",
        related="rental_order_line_id.currency_id", string="Currency")
    name = fields.Text(
        string="Description",
        related="rental_order_line_id.name")
    item_type = fields.Selection(
        related="rental_order_line_id.item_type",
        string="Item Type", required=True)
    item_code = fields.Char(
        related="rental_order_line_id.item_code", string="Item Code")

    # --- Rental Period ---
    start_date = fields.Date(string="Start Date")
    end_date = fields.Date(string="End Date")
    duration = fields.Float(
        string='Duration (Months)',
        compute='_compute_duration', store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], default='month', string="Unit")
    duration_string = fields.Char(
        string="Duration String",
        compute="_compute_duration_string")

    product_uom_qty = fields.Float(
        string='Quantity', digits='Product Unit of Measure',
        required=True, default=1.0)
    product_uom = fields.Many2one(
        'uom.uom', string='Unit of Measure',
        domain="[('category_id', '=', product_uom_category_id)]",
        ondelete="restrict")
    product_uom_category_id = fields.Many2one(
        related='product_id.uom_id.category_id')
    product_uom_txt = fields.Char(string="Uom", default="")

    price_unit = fields.Float(
        'Unit Price', required=True,
        digits='Product Price', default=0.0)
    price_subtotal = fields.Monetary(
        compute='_compute_amount', string='Subtotal', store=True)
    price_tax = fields.Float(
        compute='_compute_amount', string='Total Tax', store=True)
    price_total = fields.Monetary(
        compute='_compute_amount', string='Total', store=True)

    @api.depends('start_date', 'end_date')
    def _compute_duration(self):
        for line in self:
            if line.start_date and line.end_date:
                delta = relativedelta(line.end_date, line.start_date)
                line.duration = (
                    (delta.years * 12) + delta.months
                    + (delta.days / 30.0))
            else:
                line.duration = 0.0

    @api.depends('duration')
    def _compute_duration_string(self):
        for line in self:
            line.duration_string = f"{line.duration:.1f} Month(s)"
