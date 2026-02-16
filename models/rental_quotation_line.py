# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import get_lang
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class RentalQuotationLine(models.Model):
    _name = 'rental.quotation.line'
    _description = 'Rental Quotation Line'
    _order = 'quotation_id, sequence, id'

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    # --- Parent ---
    quotation_id = fields.Many2one(
        'rental.quotation', string='RQ Reference', required=True,
        ondelete='cascade', index=True, copy=False)
    sequence = fields.Integer(string='Sequence', default=10)

    # --- Item Type ---
    item_type = fields.Selection([
        ('unit', 'Unit'),
        ('set', 'Set'),
    ], string='Type', default='unit', required=True)

    # --- Product ---
    product_id = fields.Many2one(
        'product.product', string='Product',
        domain="[('rent_ok', '=', True), ('detailed_type', '=', 'product')]",
        change_default=True, ondelete='restrict')
    product_template_id = fields.Many2one(
        'product.template', string='Product Template',
        related='product_id.product_tmpl_id',
        domain="[('rent_ok', '=', True)]")
    name = fields.Text(string='Description', required=True)
    item_code = fields.Char(string='Item Code')

    # --- Quantity & UOM ---
    # CRITICAL: product_uom_qty must NEVER be set by any onchange method.
    # It is 100% user-controlled. The view uses force_save="1" to guarantee
    # the frontend always sends it in the save payload.
    product_uom_qty = fields.Float(
        string='Quantity', digits='Product Unit of Measure',
        required=True, default=1.0)
    product_uom_category_id = fields.Many2one(
        'uom.category', related='product_id.uom_id.category_id',
        string='UOM Category')
    product_uom = fields.Many2one(
        'uom.uom', string='Unit of Measure',
        domain="[('category_id', '=', product_uom_category_id)]",
        ondelete='restrict')
    product_uom_txt = fields.Char(string='UOM Text', default='')

    # --- Pricing ---
    price_unit = fields.Float(
        string='Unit Price', required=True,
        digits='Product Price', default=0.0)
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

    # --- Duration ---
    duration = fields.Integer(string='Duration', required=True, default=1)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], string='Unit', required=True, default='month')
    duration_string = fields.Char(
        string='Duration Str', compute='_compute_duration_str')

    # --- Dates ---
    start_date = fields.Date(
        string='Start Date', default=fields.Date.today)
    end_date = fields.Date(
        string='End Date', compute='_compute_end_date', store=True)

    # --- Components (SET only) ---
    component_line_ids = fields.One2many(
        'rental.quotation.component', 'quotation_line_id',
        string='Components')

    # --- Related / Convenience ---
    salesman_id = fields.Many2one(
        related='quotation_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(
        related='quotation_id.currency_id',
        depends=['quotation_id.currency_id'], store=True)
    company_id = fields.Many2one(
        related='quotation_id.company_id', store=True, index=True)
    order_partner_id = fields.Many2one(
        related='quotation_id.partner_id', store=True, index=True)
    state = fields.Selection(
        related='quotation_id.state', store=True, copy=False)
    product_type = fields.Selection(
        related='product_id.type', string='Product Type')

    # --- Stock Visibility (computed only, never onchange) ---
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        compute='_compute_warehouse_id', store=True)

    # --- Stock Forecast (required by view forecast widget) ---
    virtual_available_at_date = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure', string='Forecast Quantity')
    qty_available_today = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure', string='Available Today')
    free_qty_today = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure', string='Free Quantity Today')
    scheduled_date = fields.Datetime(
        string='Scheduled Date',
        compute='_compute_scheduled_date', store=True)
    forecast_expected_date = fields.Datetime(
        compute='_compute_qty_at_date', string='Expected Date')

    # --- Enhanced stock visibility ---
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
    stock_info_display = fields.Char(
        string='Stock Info', compute='_compute_stock_quantities')

    # -------------------------------------------------------------------------
    # CONSTRAINTS
    # -------------------------------------------------------------------------

    @api.constrains('end_date', 'start_date')
    def _check_dates(self):
        for line in self:
            if line.start_date and line.end_date and line.end_date < line.start_date:
                raise ValidationError(
                    _("End date cannot be before start date."))

    @api.constrains('duration')
    def _check_duration(self):
        for line in self:
            if line.duration <= 0:
                raise ValidationError(
                    _("Duration must be a positive number."))

    @api.constrains('item_type', 'component_line_ids')
    def _check_set_components(self):
        for line in self:
            if line.item_type == 'set' and not line.component_line_ids:
                raise ValidationError(
                    _("A SET item must have at least one component."))

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

    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        delta_map = {
            'hour': lambda d: relativedelta(hours=d),
            'day': lambda d: relativedelta(days=d),
            'week': lambda d: relativedelta(weeks=d),
            'month': lambda d: relativedelta(months=d),
        }
        for line in self:
            if not line.start_date or not line.duration_unit:
                line.end_date = False
                continue
            delta_fn = delta_map.get(line.duration_unit)
            line.end_date = (
                line.start_date + delta_fn(line.duration)
                if delta_fn else False)

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        labels = dict(self._fields['duration_unit'].selection)
        for line in self:
            unit_label = labels.get(line.duration_unit, '')
            line.duration_string = f"{line.duration} {unit_label}"

    @api.depends('company_id')
    def _compute_warehouse_id(self):
        for line in self:
            if line.company_id:
                line.warehouse_id = self.env['stock.warehouse'].search(
                    [('company_id', '=', line.company_id.id)], limit=1)
            else:
                line.warehouse_id = False

    @api.depends('start_date')
    def _compute_scheduled_date(self):
        for line in self:
            line.scheduled_date = (
                fields.Datetime.to_datetime(line.start_date)
                if line.start_date else False)

    @api.depends('product_id', 'product_uom_qty', 'start_date',
                 'warehouse_id', 'product_type')
    def _compute_qty_at_date(self):
        for line in self:
            if not line.product_id or line.product_type != 'product':
                line.virtual_available_at_date = 0.0
                line.qty_available_today = 0.0
                line.free_qty_today = 0.0
                line.forecast_expected_date = False
                continue

            try:
                wh = line.warehouse_id.id if line.warehouse_id else False
                product = line.product_id.with_context(warehouse=wh)
                line.qty_available_today = product.qty_available or 0.0
                line.free_qty_today = product.free_qty or 0.0

                if line.scheduled_date:
                    forecast = line.product_id.with_context(
                        warehouse=wh, to_date=line.scheduled_date)
                    line.virtual_available_at_date = forecast.virtual_available or 0.0
                    line.forecast_expected_date = False
                else:
                    line.virtual_available_at_date = line.free_qty_today
                    line.forecast_expected_date = False
            except Exception:
                line.virtual_available_at_date = 0.0
                line.qty_available_today = 0.0
                line.free_qty_today = 0.0
                line.forecast_expected_date = False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        for line in self:
            if not line.product_id:
                line.current_stock_qty = 0.0
                line.virtual_stock_qty = 0.0
                line.stock_status = 'no_product'
                line.stock_info_display = 'No Product Selected'
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

            line.stock_info_display = (
                f"Available: {line.current_stock_qty:.0f} | "
                f"Forecast: {line.virtual_stock_qty:.0f}")

    # -------------------------------------------------------------------------
    # DEFAULT & HELPERS
    # -------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('default_quotation_id'):
            quotation = self.env['rental.quotation'].browse(
                self._context['default_quotation_id'])
            if quotation:
                res.update({
                    'duration': quotation.duration,
                    'duration_unit': quotation.duration_unit,
                    'start_date': quotation.start_date,
                })
        return res

    def _get_rental_pricing_list(self, product):
        """Return dict of {unit: price} from product's rental pricing."""
        if not product or not product.rental_pricing_ids:
            return False
        return {p.unit: p.price for p in product.rental_pricing_ids}

    def _compute_rental_price(self):
        """Compute and set price_unit based on rental pricing config.

        This method ONLY sets price_unit. It never touches product_uom_qty.
        """
        if not self.product_id:
            return

        product = self.product_id.with_context(
            partner=self.quotation_id.partner_id,
            quantity=self.product_uom_qty,
            date=self.quotation_id.date_order,
            pricelist=self.quotation_id.pricelist_id.id,
            uom=self.product_uom.id if self.product_uom else False)

        if not (self.quotation_id.pricelist_id and self.quotation_id.partner_id):
            return

        pricing = self._get_rental_pricing_list(product)
        if not pricing:
            raise ValidationError(
                _("Rental price for duration '%s' is not configured for this "
                  "product. Please contact the administrator or choose a "
                  "different duration.") % self.duration_unit)

        if self.duration_unit not in pricing:
            available = ', '.join(pricing.keys())
            raise ValidationError(
                _("This product is not available for rental by %s. "
                  "Available options: %s.") % (self.duration_unit, available))

        rental_price = pricing[self.duration_unit] * self.duration
        self.price_unit = product._get_tax_included_unit_price(
            self.company_id,
            self.quotation_id.currency_id,
            self.quotation_id.date_order,
            'sale',
            fiscal_position=self.quotation_id.fiscal_position_id,
            product_price_unit=rental_price,
            product_currency=self.quotation_id.currency_id)

    # -------------------------------------------------------------------------
    # CRUD OVERRIDES (with debug logging)
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for i, vals in enumerate(vals_list):
            _logger.info("=" * 60)
            _logger.info("RENTAL LINE CREATE - Line %d", i)
            _logger.info("  product_uom_qty: %s",
                         vals.get('product_uom_qty', '*** NOT IN VALS ***'))
            _logger.info("  product_uom: %s",
                         vals.get('product_uom', '*** NOT IN VALS ***'))
            _logger.info("  product_id: %s",
                         vals.get('product_id', '*** NOT IN VALS ***'))
            _logger.info("  All keys: %s", sorted(vals.keys()))
            _logger.info("=" * 60)

            if 'product_uom_qty' not in vals:
                _logger.warning(
                    "product_uom_qty MISSING from create vals! "
                    "Frontend did not send it.")

        records = super().create(vals_list)
        for rec in records:
            _logger.info(
                "AFTER CREATE - ID %s: qty=%s, uom=%s, product=%s",
                rec.id, rec.product_uom_qty,
                rec.product_uom.name if rec.product_uom else 'None',
                rec.product_id.display_name if rec.product_id else 'None')
        return records

    def write(self, vals):
        if 'product_uom_qty' in vals:
            _logger.info("RENTAL LINE WRITE - qty=%s for IDs=%s",
                         vals['product_uom_qty'], self.ids)
        return super().write(vals)

    # -------------------------------------------------------------------------
    # ONCHANGE METHODS
    #
    # Rules:
    #   1. NEVER set product_uom_qty — it is user-controlled only
    #   2. NEVER use self.update(dict) — use direct field assignment
    #   3. ONE onchange per trigger field — no duplicates
    # -------------------------------------------------------------------------

    @api.onchange('item_type')
    def _onchange_item_type(self):
        """Handle switching between UNIT and SET types."""
        if self.item_type == 'set':
            self.product_id = False
            self.product_uom_txt = 'SET'
        elif self.item_type == 'unit':
            if self.component_line_ids:
                self.component_line_ids = [(5, 0, 0)]
            self.product_uom_txt = ''

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """Handle product selection. Sets description, UOM, and price.
        
        NEVER touches product_uom_qty.
        """
        if not self.product_id:
            self.product_uom = False
            self.product_uom_txt = 'SET' if self.item_type == 'set' else ''
            self.price_unit = 0.0
            self.name = False
            return

        # Description
        lang = get_lang(self.env, self.quotation_id.partner_id.lang).code
        product = self.product_id.with_context(lang=lang)
        self.name = product.display_name
        self.item_code = product.item_code_ref

        # UOM — only update if not set or category mismatch
        if (not self.product_uom
                or self.product_uom.category_id != self.product_id.uom_id.category_id):
            self.product_uom = self.product_id.uom_id
            self.product_uom_txt = self.product_id.uom_id.name

        # Price
        self._compute_rental_price()

    @api.onchange('duration', 'duration_unit')
    def _onchange_duration(self):
        """Recalculate price when duration changes."""
        if self.product_id:
            self._compute_rental_price()

    @api.onchange('component_line_ids')
    def _onchange_component_line_ids(self):
        """Sum component subtotals for SET items."""
        if self.item_type == 'set':
            self.price_unit = sum(
                c.price_subtotal for c in self.component_line_ids)

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_view_stock_forecast(self):
        """Open the stock forecast page for this product."""
        self.ensure_one()
        if not self.product_id:
            return False

        wh = (self.warehouse_id.id
              if self.warehouse_id
              else self.env.user.company_id.warehouse_id.id)
        action = self.env.ref(
            'stock.stock_replenishment_product_product_action').read()[0]
        action.update({
            'name': f'Stock Forecast - {self.product_id.display_name}',
            'domain': [('product_id', '=', self.product_id.id)],
            'context': {
                'search_default_product_id': self.product_id.id,
                'default_product_id': self.product_id.id,
                'default_warehouse_id': wh,
                'search_default_warehouse_id': wh,
            },
        })
        return action