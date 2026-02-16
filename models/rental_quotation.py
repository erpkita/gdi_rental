# -*- coding: utf-8 -*-

import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class RentalQuotation(models.Model):
    _name = 'rental.quotation'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Rental Quotation'
    _order = 'date_order desc, id desc'

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    name = fields.Char(
        string="RQ Reference", required=True, copy=False,
        readonly=True, index=True, default=lambda self: _('New'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirm', 'Confirmed'),
        ('Cancel', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    # --- Partner ---
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]")
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address', required=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address', required=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)
    validity_date = fields.Date(string="Expiration Date", copy=False)

    # --- Dates & Duration ---
    date_order = fields.Datetime(
        string='Order Date', required=True, readonly=True, index=True,
        copy=False, default=fields.Datetime.now)
    create_date = fields.Datetime(
        string='Creation Date', readonly=True, index=True)
    start_date = fields.Date(
        string="Start Date", default=fields.Date.today, required=True)
    end_date = fields.Date(
        string="End Date", compute='_compute_end_date', store=True)
    duration = fields.Integer(
        string="Duration", default=1, required=True,
        compute='_compute_duration_from_lines',
        inverse='_inverse_duration', store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], string="Unit", required=True, default='month',
        compute='_compute_duration_from_lines',
        inverse='_inverse_duration', store=True)
    duration_string = fields.Char(
        string="Duration Str", compute='_compute_duration_str')

    # --- Company / Currency ---
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', required=True,
        check_company=True,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        tracking=1)
    currency_id = fields.Many2one(
        related='pricelist_id.currency_id',
        depends=['pricelist_id'], store=True, ondelete='restrict')
    fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string='Fiscal Position',
        domain="[('company_id', '=', company_id)]", check_company=True)
    tax_country_id = fields.Many2one(
        'res.country', compute='_compute_tax_country_id',
        compute_sudo=True)

    # --- Salesperson ---
    user_id = fields.Many2one(
        'res.users', string='Salesperson', index=True, tracking=2,
        default=lambda self: self.env.user)

    # --- Lines ---
    order_line = fields.One2many(
        'rental.quotation.line', 'quotation_id', string='Quotation Lines',
        copy=True, auto_join=True)

    # --- Totals ---
    amount_untaxed = fields.Monetary(
        string='Untaxed Amount', store=True, compute='_compute_amounts', tracking=5)
    amount_tax = fields.Monetary(
        string='Taxes', store=True, compute='_compute_amounts')
    amount_total = fields.Monetary(
        string='Total', store=True, compute='_compute_amounts', tracking=4)
    tax_totals_json = fields.Char(compute='_compute_tax_totals_json')
    currency_rate = fields.Float(
        string="Currency Rate", compute='_compute_currency_rate',
        store=True, digits=(12, 6))

    # --- Misc ---
    note = fields.Html('Terms and conditions')

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------

    @api.depends('order_line.price_total')
    def _compute_amounts(self):
        for order in self:
            untaxed = tax = 0.0
            for line in order.order_line:
                untaxed += line.price_subtotal
                tax += line.price_tax
            order.amount_untaxed = untaxed
            order.amount_tax = tax
            order.amount_total = untaxed + tax

    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        delta_map = {
            'hour': lambda d: relativedelta(hours=d),
            'day': lambda d: relativedelta(days=d),
            'week': lambda d: relativedelta(weeks=d),
            'month': lambda d: relativedelta(months=d),
        }
        for rec in self:
            if not rec.start_date or not rec.duration_unit:
                rec.end_date = False
                continue
            delta_fn = delta_map.get(rec.duration_unit)
            rec.end_date = rec.start_date + delta_fn(rec.duration) if delta_fn else False

    @api.depends('order_line', 'order_line.duration', 'order_line.duration_unit')
    def _compute_duration_from_lines(self):
        for rec in self:
            if not rec.order_line:
                # Keep current values when no lines exist
                continue
            longest_days = 0
            best_duration = rec.duration or 1
            best_unit = rec.duration_unit or 'month'
            for line in rec.order_line:
                line_days = self._to_days(line.duration, line.duration_unit)
                if line_days > longest_days:
                    longest_days = line_days
                    best_duration = line.duration
                    best_unit = line.duration_unit
            rec.duration = best_duration
            rec.duration_unit = best_unit

    def _inverse_duration(self):
        pass  # Allow direct edits from the form

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        labels = dict(self._fields['duration_unit'].selection)
        for rec in self:
            unit_label = labels.get(rec.duration_unit, '')
            rec.duration_string = f"{rec.duration} {unit_label}"

    @api.depends('order_line.tax_id', 'order_line.price_unit',
                 'amount_total', 'amount_untaxed')
    def _compute_tax_totals_json(self):
        def _compute_taxes(line):
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            return line.tax_id._origin.compute_all(
                price, line.quotation_id.currency_id,
                line.product_uom_qty, product=line.product_id,
                partner=line.quotation_id.partner_shipping_id)

        AccountMove = self.env['account.move']
        for order in self:
            tax_data = AccountMove._prepare_tax_lines_data_for_totals_from_object(
                order.order_line, _compute_taxes)
            tax_totals = AccountMove._get_tax_totals(
                order.partner_id, tax_data,
                order.amount_total, order.amount_untaxed, order.currency_id)
            order.tax_totals_json = json.dumps(tax_totals)

    @api.depends('pricelist_id', 'date_order', 'company_id')
    def _compute_currency_rate(self):
        for order in self:
            if order.company_id and order.company_id.currency_id and order.currency_id:
                order.currency_rate = self.env['res.currency']._get_conversion_rate(
                    order.company_id.currency_id, order.currency_id,
                    order.company_id, order.date_order)
            else:
                order.currency_rate = 1.0

    def _compute_tax_country_id(self):
        for rec in self:
            if rec.fiscal_position_id.foreign_vat:
                rec.tax_country_id = rec.fiscal_position_id.country_id
            else:
                rec.tax_country_id = rec.company_id.account_fiscal_country_id

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    @staticmethod
    def _to_days(duration, unit):
        """Convert duration to approximate days for comparison."""
        multipliers = {'hour': 1 / 24, 'day': 1, 'week': 7, 'month': 30}
        return duration * multipliers.get(unit, 0)

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            seq_date = None
            if 'date_order' in vals:
                seq_date = fields.Datetime.context_timestamp(
                    self, fields.Datetime.to_datetime(vals['date_order']))
            seq = self.env['ir.sequence'].next_by_code(
                'rental.quotation', sequence_date=seq_date) or _('New')
            vals['name'] = f"RQ{seq}"
        return super().create(vals)

    def unlink(self):
        return super().unlink()

    # -------------------------------------------------------------------------
    # ONCHANGES
    # -------------------------------------------------------------------------

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if not self.partner_id:
            self.partner_invoice_id = False
            self.partner_shipping_id = False
            self.fiscal_position_id = False
            return

        self = self.with_company(self.company_id)
        addr = self.partner_id.address_get(['delivery', 'invoice'])
        self.partner_invoice_id = addr['invoice']
        self.partner_shipping_id = addr['delivery']
        self.pricelist_id = (
            self.partner_id.property_product_pricelist.id
            if self.partner_id.property_product_pricelist else False)

        partner_user = (
            self.partner_id.user_id
            or self.partner_id.commercial_partner_id.user_id)
        if partner_user and not self.env.context.get('not_self_saleperson'):
            self.user_id = partner_user

    @api.onchange('partner_shipping_id', 'partner_id', 'company_id')
    def _onchange_partner_shipping_id(self):
        self.fiscal_position_id = (
            self.env['account.fiscal.position']
            .with_company(self.company_id)
            .get_fiscal_position(self.partner_id.id, self.partner_shipping_id.id))

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_cancel(self):
        self.write({'state': 'Cancel'})

    def action_print_quotation(self):
        self.ensure_one()
        if self.state == 'draft':
            self.write({'state': 'sent'})
        return self.env.ref(
            'gdi_rental.gdi_action_report_rental_quotation'
        ).report_action(self)

    def action_send_quotation(self):
        self.write({'state': 'sent'})

    def action_confirm(self):
        self.ensure_one()
        if not self.customer_reference or not self.customer_po_number:
            raise ValidationError(
                _("Please input Customer Reference and Customer Ref. PO !"))

        # Create Rental Order from Quotation
        order_vals = self._prepare_rental_order()
        rental_order = self.env['gdi.rental.order'].create(order_vals)

        for line in self.order_line:
            line_vals = self._prepare_rental_order_line(line)
            line_vals['order_id'] = rental_order.id
            self.env['gdi.rental.order.line'].create(line_vals)

        self.write({'state': 'confirm'})
        return self._action_view_rental_order(rental_order)

    def _action_view_rental_order(self, rental_order):
        action = self.env['ir.actions.actions']._for_xml_id(
            'gdi_rental.action_gdi_rental_order')
        form_view = [(
            self.env.ref('gdi_rental.view_gdi_rental_order_form').id, 'form')]
        action['views'] = form_view + [
            (state, view) for state, view in action.get('views', [])
            if view != 'form']
        action['res_id'] = rental_order.id
        return action

    # -------------------------------------------------------------------------
    # QUOTATION → ORDER PREPARATION
    # -------------------------------------------------------------------------

    def _prepare_rental_order(self):
        self.ensure_one()
        return {
            'partner_id': self.partner_id.id,
            'partner_invoice_id': self.partner_invoice_id.id,
            'partner_shipping_id': self.partner_shipping_id.id,
            'pricelist_id': self.pricelist_id.id,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': self.user_id.id,
            'quotation_id': self.id,
            'company_id': self.company_id.id,
            'note': self.note or False,
            'date_order': self.date_order,
            'currency_id': self.currency_id.id,
            'fiscal_position_id': self.fiscal_position_id.id,
            'duration': self.duration,
            'duration_unit': self.duration_unit,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'order_line': [],
        }

    def _prepare_rental_order_line(self, line):
        vals = {
            'name': line.name,
            'item_type': line.item_type,
            'item_code': line.item_code,
            'product_id': line.product_id.id or False,
            'product_uom': line.product_uom.id or False,
            'product_uom_qty': line.product_uom_qty,
            'product_uom_txt': line.product_uom_txt or '',
            'price_unit': line.price_unit,
            'tax_id': line.tax_id.ids or False,
            'duration': line.duration,
            'duration_unit': line.duration_unit,
            'start_date': line.start_date,
            'end_date': line.end_date,
        }
        if line.item_type == 'set' and line.component_line_ids:
            vals['component_line_ids'] = [
                (0, 0, {
                    'product_id': c.product_id.id,
                    'name': c.name or '',
                    'price_unit': c.price_unit,
                    'product_uom_qty': c.product_uom_qty,
                    'product_uom': c.product_uom.id,
                }) for c in line.component_line_ids
            ]
        return vals