# -*- coding: utf-8 -*-

import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import get_lang
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class GDIRentalQuotation(models.Model):
    _name = "gdi.rental.quotation"
    _description = "GDI Rental Quotation"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date_order desc, id desc'

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    name = fields.Char(
        string="RQ Reference", required=True, copy=False,
        readonly=True, index=True,
        default=lambda self: _('New'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirm', 'Confirmed'),
        ('cancel', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    # --- Partner ---
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]")
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)
    validity_date = fields.Date(string="Expiration Date", copy=False)

    # --- Dates ---
    date_order = fields.Datetime(
        string='Order Date', required=True, index=True, copy=False,
        default=fields.Datetime.now)
    rental_start_date = fields.Date(
        string="Rental Start Date",
        compute='_compute_rental_dates', store=True, readonly=False)
    rental_end_date = fields.Date(
        string="Rental End Date",
        compute='_compute_rental_dates', store=True, readonly=False)
    rental_duration = fields.Float(
        string="Duration (Months)",
        compute='_compute_rental_duration', store=True, readonly=False)

    # --- Company / Currency ---
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', required=True,
        check_company=True,
        default=lambda self: self.env['product.pricelist'].search(
            [('company_id', 'in', (False, self.env.company.id))], limit=1),
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        tracking=1)
    currency_id = fields.Many2one(
        related='pricelist_id.currency_id',
        depends=['pricelist_id'], store=True, ondelete='restrict')
    fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string='Fiscal Position',
        domain="[('company_id', '=', company_id)]", check_company=True)
    tax_country_id = fields.Many2one(
        'res.country', compute='_compute_tax_country_id', compute_sudo=True)

    # --- Salesperson ---
    user_id = fields.Many2one(
        'res.users', string='Salesperson', index=True, tracking=2,
        default=lambda self: self.env.user)

    # --- Lines ---
    order_line = fields.One2many(
        'gdi.rental.quotation.line', 'quotation_id',
        string='Quotation Lines', copy=True, auto_join=True)

    # --- Totals ---
    amount_untaxed = fields.Monetary(
        string='Untaxed Amount', store=True,
        compute='_compute_amounts', tracking=5)
    amount_tax = fields.Monetary(
        string='Taxes', store=True, compute='_compute_amounts')
    amount_total = fields.Monetary(
        string='Total', store=True,
        compute='_compute_amounts', tracking=4)
    tax_totals_json = fields.Char(compute='_compute_tax_totals_json')
    currency_rate = fields.Float(
        string="Currency Rate", compute='_compute_currency_rate',
        store=True, digits=(12, 6))

    # --- Misc ---
    note = fields.Html('Terms and conditions')

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------

    @api.depends('order_line.start_date', 'order_line.end_date')
    def _compute_rental_dates(self):
        for record in self:
            dates_start = record.order_line.mapped('start_date')
            dates_end = record.order_line.mapped('end_date')
            valid_starts = [d for d in dates_start if d]
            valid_ends = [d for d in dates_end if d]
            if valid_starts:
                record.rental_start_date = min(valid_starts)
            if valid_ends:
                record.rental_end_date = max(valid_ends)

    @api.depends('rental_start_date', 'rental_end_date')
    def _compute_rental_duration(self):
        for record in self:
            if record.rental_start_date and record.rental_end_date:
                delta = relativedelta(
                    record.rental_end_date, record.rental_start_date)
                record.rental_duration = (
                    (delta.years * 12) + delta.months + (delta.days / 30.0))
            else:
                record.rental_duration = 0.0

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
            tax_data = (
                AccountMove._prepare_tax_lines_data_for_totals_from_object(
                    order.order_line, _compute_taxes))
            tax_totals = AccountMove._get_tax_totals(
                order.partner_id, tax_data,
                order.amount_total, order.amount_untaxed,
                order.currency_id)
            order.tax_totals_json = json.dumps(tax_totals)

    @api.depends('pricelist_id', 'date_order', 'company_id')
    def _compute_currency_rate(self):
        for order in self:
            if (order.company_id and order.company_id.currency_id
                    and order.currency_id):
                order.currency_rate = (
                    self.env['res.currency']._get_conversion_rate(
                        order.company_id.currency_id, order.currency_id,
                        order.company_id, order.date_order))
            else:
                order.currency_rate = 1.0

    def _compute_tax_country_id(self):
        for rec in self:
            if rec.fiscal_position_id.foreign_vat:
                rec.tax_country_id = rec.fiscal_position_id.country_id
            else:
                rec.tax_country_id = rec.company_id.account_fiscal_country_id

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
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'gdi.rental.quotation',
                sequence_date=seq_date) or _('New')
        return super().create(vals)

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
        if self.partner_id:
            self.fiscal_position_id = (
                self.env['account.fiscal.position']
                .with_company(self.company_id)
                .get_fiscal_position(
                    self.partner_id.id,
                    self.partner_shipping_id.id
                    if self.partner_shipping_id else False))

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_send(self):
        self.write({'state': 'sent'})

    def action_confirm(self):
        self.ensure_one()
        if not self.order_line:
            raise ValidationError(
                _("Please add at least one quotation line."))

        # Create Rental Order from Quotation
        order_vals = self._prepare_rental_order()
        rental_order = self.env['gdi.rental.order'].create(order_vals)

        for line in self.order_line:
            line_vals = self._prepare_rental_order_line(line)
            line_vals['order_id'] = rental_order.id
            self.env['gdi.rental.order.line'].create(line_vals)

        self.write({'state': 'confirm'})
        return self._action_view_rental_order(rental_order)

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_print_quotation(self):
        self.ensure_one()
        if self.state == 'draft':
            self.write({'state': 'sent'})
        return self.env.ref(
            'gdi_rental.gdi_action_report_rental_quotation'
        ).report_action(self)

    def _action_view_rental_order(self, rental_order):
        action = self.env['ir.actions.actions']._for_xml_id(
            'gdi_rental.action_gdi_rental_order')
        form_view = [(
            self.env.ref('gdi_rental.view_gdi_rental_order_form').id,
            'form')]
        action['views'] = form_view + [
            (state, view) for state, view in action.get('views', [])
            if view != 'form']
        action['res_id'] = rental_order.id
        return action

    # -------------------------------------------------------------------------
    # QUOTATION -> ORDER PREPARATION
    # -------------------------------------------------------------------------

    def _prepare_rental_order(self):
        self.ensure_one()
        return {
            'partner_id': self.partner_id.id,
            'partner_invoice_id': (
                self.partner_invoice_id.id
                if self.partner_invoice_id else self.partner_id.id),
            'partner_shipping_id': (
                self.partner_shipping_id.id
                if self.partner_shipping_id else self.partner_id.id),
            'pricelist_id': self.pricelist_id.id,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': self.user_id.id,
            'quotation_id': self.id,
            'company_id': self.company_id.id,
            'note': self.note or False,
            'date_order': self.date_order,
            'currency_id': self.currency_id.id,
            'fiscal_position_id': (
                self.fiscal_position_id.id
                if self.fiscal_position_id else False),
            'start_date': (
                self.rental_start_date or fields.Date.today()),
            'end_date': self.rental_end_date or False,
            'duration_unit': 'month',
        }

    def _prepare_rental_order_line(self, line):
        vals = {
            'name': line.name or '',
            'item_type': line.line_type,
            'item_code': line.item_code or '',
            'product_id': line.product_id.id or False,
            'product_uom': line.product_uom.id or False,
            'product_uom_qty': line.product_uom_qty,
            'product_uom_txt': line.product_uom_txt or '',
            'price_unit': line.price_unit,
            'tax_id': line.tax_id.ids or False,
            'discount': line.discount,
            'start_date': (
                line.start_date
                or self.rental_start_date
                or fields.Date.today()),
            'end_date': (
                line.end_date
                or self.rental_end_date
                or False),
        }
        # For SET type: expand rental set into component lines
        if (line.line_type == 'set'
                and line.rental_set_id
                and line.rental_set_id.line_ids):
            vals['component_line_ids'] = [
                (0, 0, {
                    'product_id': set_line.product_id.id,
                    'name': set_line.product_id.display_name or '',
                    'product_uom_qty': (
                        set_line.quantity * line.product_uom_qty),
                    'product_uom': set_line.product_id.uom_id.id,
                    'price_unit': 0.0,
                }) for set_line in line.rental_set_id.line_ids
            ]
        return vals


class GDIRentalQuotationLine(models.Model):
    _name = "gdi.rental.quotation.line"
    _description = "GDI Rental Quotation Line"
    _order = "quotation_id, sequence, id"

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    # --- Parent ---
    quotation_id = fields.Many2one(
        'gdi.rental.quotation', string='Quotation', required=True,
        ondelete='cascade', index=True, auto_join=True)
    sequence = fields.Integer(default=10, index=True)

    # --- Related convenience ---
    company_id = fields.Many2one(
        related='quotation_id.company_id', store=True,
        string='Company', readonly=True)
    salesman_id = fields.Many2one(
        related='quotation_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(
        related='quotation_id.currency_id',
        depends=['quotation_id.currency_id'], store=True)
    order_partner_id = fields.Many2one(
        related='quotation_id.partner_id', store=True, index=True)
    state = fields.Selection(
        related='quotation_id.state', store=True, copy=False)

    # --- Type Selection ---
    line_type = fields.Selection([
        ('unit', 'Unit'),
        ('set', 'Set')
    ], string='Type', default='unit', required=True)

    rental_set_id = fields.Many2one(
        'gdi.rental.set', string="Rental Set")
    rental_set_line_ids = fields.One2many(
        related='rental_set_id.line_ids', string="Set Components", readonly=True)
    item_code = fields.Char(string="Product Code")

    # --- Product ---
    product_id = fields.Many2one(
        'product.product', string='Product',
        domain="[('sale_ok', '=', True), ('rent_ok', '=', True), "
               "'|', ('company_id', '=', False), "
               "('company_id', '=', company_id)]",
        change_default=True, ondelete='restrict')
    product_template_id = fields.Many2one(
        'product.template', string='Product Template',
        related='product_id.product_tmpl_id',
        domain="[('rent_ok', '=', True)]")
    name = fields.Text(string='Description')

    # --- Quantity & UOM ---
    # CRITICAL: product_uom_qty must NEVER be set by any onchange method.
    # It is 100% user-controlled. The view uses force_save="1".
    product_uom_qty = fields.Float(
        string="Quantity", default=1.0,
        digits='Product Unit of Measure')
    product_uom_category = fields.Many2one(
        related='product_id.uom_id.category_id',
        string="UOM Category", store=True, readonly=True)
    product_uom = fields.Many2one(
        'uom.uom', string="Unit of Measure",
        domain="[('category_id', '=', product_uom_category)]")
    product_uom_txt = fields.Char(string='UOM Text', default='')
    product_type = fields.Selection(
        related='product_id.type', string='Product Type')

    # --- Pricing ---
    price_unit = fields.Float(
        string='Unit Price', digits='Product Price', default=0.0)
    discount = fields.Float(
        string='Discount (%)', digits='Discount', default=0.0)
    price_subtotal = fields.Monetary(
        string='Subtotal', store=True, compute='_compute_amount')
    price_tax = fields.Float(
        string='Total Tax', store=True, compute='_compute_amount')
    price_total = fields.Monetary(
        string='Total', store=True, compute='_compute_amount')
    tax_id = fields.Many2many(
        'account.tax', string='Taxes',
        context={'active_test': False})

    # --- Rental Period ---
    start_date = fields.Date(string="Start Date")
    end_date = fields.Date(string="End Date")
    duration = fields.Float(
        string="Duration (Months)",
        compute='_compute_duration', store=True)

    # --- Stock Information ---
    virtual_available = fields.Float(
        related='product_id.virtual_available',
        string="Forecasted Qty", readonly=True)
    qty_available = fields.Float(
        related='product_id.qty_available',
        string="On Hand Qty", readonly=True)

    # --- Stock Visibility ---
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        compute='_compute_warehouse_id', store=True)
    current_stock_qty = fields.Float(
        string='Current Stock', compute='_compute_stock_quantities',
        digits='Product Unit of Measure')
    virtual_stock_qty = fields.Float(
        string='Forecast Stock', compute='_compute_stock_quantities',
        digits='Product Unit of Measure')
    stock_status = fields.Selection([
        ('in_stock', 'In Stock'),
        ('low_stock', 'Low Stock'),
        ('out_of_stock', 'Out of Stock'),
        ('no_product', 'No Product Selected'),
    ], string='Stock Status', compute='_compute_stock_quantities')

    # -------------------------------------------------------------------------
    # CONSTRAINTS
    # -------------------------------------------------------------------------

    @api.constrains('line_type', 'rental_set_id')
    def _check_rental_set_required(self):
        for line in self:
            if line.line_type == 'set' and not line.rental_set_id:
                raise ValidationError(
                    _("Rental Set is required for lines of type 'Set'."))

    @api.constrains('end_date', 'start_date')
    def _check_dates(self):
        for line in self:
            if (line.start_date and line.end_date
                    and line.end_date < line.start_date):
                raise ValidationError(
                    _("End date cannot be before start date."))

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(
                price, line.quotation_id.currency_id,
                line.product_uom_qty,
                product=line.product_id,
                partner=line.quotation_id.partner_shipping_id)
            line.price_tax = sum(
                t.get('amount', 0.0) for t in taxes.get('taxes', []))
            line.price_total = taxes['total_included']
            line.price_subtotal = taxes['total_excluded']

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

    @api.depends('company_id')
    def _compute_warehouse_id(self):
        for line in self:
            if line.company_id:
                line.warehouse_id = self.env['stock.warehouse'].search(
                    [('company_id', '=', line.company_id.id)], limit=1)
            else:
                line.warehouse_id = False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        for line in self:
            if not line.product_id:
                line.current_stock_qty = 0.0
                line.virtual_stock_qty = 0.0
                line.stock_status = 'no_product'
                continue

            wh = (line.warehouse_id.id
                  if line.warehouse_id
                  else line.env.user.company_id.warehouse_id.id)
            product = line.product_id.with_context(warehouse=wh)
            line.current_stock_qty = product.qty_available
            line.virtual_stock_qty = product.virtual_available

            if line.current_stock_qty > 0:
                if line.current_stock_qty >= line.product_uom_qty:
                    line.stock_status = 'in_stock'
                else:
                    line.stock_status = 'low_stock'
            else:
                line.stock_status = 'out_of_stock'

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _get_rental_pricing_list(self, product):
        """Return dict of {unit: price} from product's rental pricing."""
        if not product or not product.rental_pricing_ids:
            return False
        return {p.unit: p.price for p in product.rental_pricing_ids}

    def _compute_rental_price(self):
        """Compute price_unit from rental pricing. NEVER touches qty."""
        if not self.product_id:
            return

        product = self.product_id.with_context(
            partner=self.quotation_id.partner_id,
            quantity=self.product_uom_qty,
            date=self.quotation_id.date_order,
            pricelist=self.quotation_id.pricelist_id.id,
            uom=self.product_uom.id if self.product_uom else False)

        if not (self.quotation_id.pricelist_id
                and self.quotation_id.partner_id):
            return

        pricing = self._get_rental_pricing_list(product)
        if not pricing:
            self.price_unit = product.lst_price
            return

        # Compute duration in months from dates
        duration_months = 1.0
        if self.start_date and self.end_date:
            delta = relativedelta(self.end_date, self.start_date)
            duration_months = max(
                1.0,
                (delta.years * 12) + delta.months + (delta.days / 30.0))

        # Find best pricing match (prefer 'month')
        if 'month' in pricing:
            rental_price = pricing['month'] * duration_months
        elif 'week' in pricing:
            rental_price = pricing['week'] * (duration_months * 4.33)
        elif 'day' in pricing:
            rental_price = pricing['day'] * (duration_months * 30)
        else:
            first_unit = list(pricing.keys())[0]
            rental_price = pricing[first_unit]

        self.price_unit = product._get_tax_included_unit_price(
            self.company_id,
            self.quotation_id.currency_id,
            self.quotation_id.date_order,
            'sale',
            fiscal_position=self.quotation_id.fiscal_position_id,
            product_price_unit=rental_price,
            product_currency=self.quotation_id.currency_id)

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'product_uom_qty' not in vals:
                _logger.warning(
                    "product_uom_qty MISSING from create vals! "
                    "Frontend did not send it.")
        return super().create(vals_list)

    def write(self, vals):
        if 'product_uom_qty' in vals:
            _logger.info(
                "GDI RQ LINE WRITE - qty=%s for IDs=%s",
                vals['product_uom_qty'], self.ids)
        return super().write(vals)

    # -------------------------------------------------------------------------
    # ONCHANGES
    #
    # Rules:
    #   1. NEVER set product_uom_qty - it is user-controlled only
    #   2. NEVER use self.update(dict) - use direct field assignment
    #   3. ONE onchange per trigger field - no duplicates
    # -------------------------------------------------------------------------

    @api.onchange('line_type')
    def _onchange_line_type(self):
        """Handle switching between UNIT and SET types."""
        if self.line_type == 'set':
            self.product_id = False
            self.product_uom_txt = 'SET'
        else:
            self.rental_set_id = False
            self.product_uom_txt = ''

    @api.onchange('rental_set_id')
    def _onchange_rental_set_id(self):
        if self.line_type == 'set' and self.rental_set_id:
            self.item_code = self.rental_set_id.code
            self.name = (
                self.rental_set_id.description
                or self.rental_set_id.name)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """Set description, UOM, and price. NEVER touches product_uom_qty."""
        if not self.product_id:
            self.product_uom = False
            self.product_uom_txt = (
                'SET' if self.line_type == 'set' else '')
            self.price_unit = 0.0
            self.name = False
            return

        if self.line_type == 'unit':
            lang = get_lang(
                self.env, self.quotation_id.partner_id.lang).code
            product = self.product_id.with_context(lang=lang)
            self.name = product.display_name
            self.item_code = product.item_code_ref

            if (not self.product_uom
                    or (self.product_uom.category_id
                        != self.product_id.uom_id.category_id)):
                self.product_uom = self.product_id.uom_id
                self.product_uom_txt = self.product_id.uom_id.name

            self._compute_rental_price()

    @api.onchange('start_date', 'end_date')
    def _onchange_dates(self):
        """Recalculate price when dates change (duration changes)."""
        if (self.product_id and self.line_type == 'unit'
                and self.start_date and self.end_date):
            self._compute_rental_price()

    @api.onchange('quotation_id')
    def _onchange_quotation_id(self):
        if self.quotation_id:
            self.start_date = self.quotation_id.rental_start_date
            self.end_date = self.quotation_id.rental_end_date
