# -*- coding: utf-8 -*-

import logging
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import get_lang
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class GdiRentalOrderLine(models.Model):
    _name = 'gdi.rental.order.line'
    _description = 'Rental Order Line'
    _order = 'order_id, sequence, id'

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    # --- Parent ---
    order_id = fields.Many2one(
        'gdi.rental.order', string='RO Reference', required=True,
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
        domain="[('sale_ok', '=', True), "
               "'|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        change_default=True, ondelete='restrict')
    product_template_id = fields.Many2one(
        'product.template', string='Product Template',
        related='product_id.product_tmpl_id',
        domain="[('sale_ok', '=', True)]")
    name = fields.Text(string='Description', required=True)
    item_code = fields.Char(string='Item Code', required=True)

    # --- Quantity & UOM ---
    product_uom_qty = fields.Float(
        string='Quantity', digits='Product Unit of Measure',
        required=True, default=1.0)
    product_uom_category_id = fields.Many2one(
        'uom.category', related='product_id.uom_id.category_id')
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
        'account.tax', string='Taxes', context={'active_test': False})

    # --- Duration ---
    duration = fields.Integer(string='Duration', required=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], string='Unit', required=True)
    duration_string = fields.Char(
        string='Duration Str', compute='_compute_duration_str')

    # --- Dates ---
    start_date = fields.Date(
        string='Start Date',
        compute='_compute_start_date', store=True,
        inverse='_inverse_start_date')
    end_date = fields.Date(
        string='End Date',
        compute='_compute_end_date', store=True,
        inverse='_inverse_end_date')
    date_definition_level = fields.Selection(
        related='order_id.date_definition_level',
        string='Date Definition Level')

    # --- Components (SET only) ---
    component_line_ids = fields.One2many(
        'rental.order.component', 'order_line_id',
        string='Components')

    # --- Rental State ---
    rental_state = fields.Selection([
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('hireoff', 'Hired-Off'),
    ], string='Rental Status', default='draft')

    # --- Related / Convenience ---
    salesman_id = fields.Many2one(
        related='order_id.user_id', store=True, string='Salesperson')
    currency_id = fields.Many2one(
        related='order_id.currency_id',
        depends=['order_id.currency_id'], store=True)
    company_id = fields.Many2one(
        related='order_id.company_id', store=True, index=True)
    order_partner_id = fields.Many2one(
        related='order_id.partner_id', store=True, index=True)
    state = fields.Selection(
        related='order_id.state', store=True, copy=False)
    product_type = fields.Selection(
        related='product_id.type', string='Product Type')

    # --- Stock Moves ---
    stock_move_ids = fields.One2many(
        'stock.move', 'ro_line_id', string='Stock Moves')

    # --- Stock Availability (computed) ---
    available_qty = fields.Float(
        string='Available Qty', compute='_compute_available_qty')
    src_location_id = fields.Many2one(
        'stock.location', string='Source Location')
    available_src_location_ids = fields.Many2many(
        'stock.location', string='Src Location Ids',
        compute='_compute_available_src_location')
    available_src_location_txt = fields.Text(
        string='Available Src Location',
        compute='_compute_available_src_location')

    # --- Stock Forecast ---
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
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        compute='_compute_warehouse_id', store=True, check_company=True)
    is_mto = fields.Boolean(
        string='Is Made to Order', compute='_compute_is_mto')
    qty_to_delivery = fields.Float(
        string='Quantity to Deliver',
        compute='_compute_qty_to_deliver',
        digits='Product Unit of Measure')

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
    # COMPUTE — AMOUNTS
    # -------------------------------------------------------------------------

    @api.depends('product_uom_qty', 'discount', 'price_unit', 'tax_id')
    def _compute_amount(self):
        for line in self:
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = line.tax_id.compute_all(
                price, line.order_id.currency_id,
                line.product_uom_qty,
                product=line.product_id,
                partner=line.order_id.partner_shipping_id)
            line.price_tax = sum(
                t.get('amount', 0.0) for t in taxes.get('taxes', []))
            line.price_total = taxes['total_included']
            line.price_subtotal = taxes['total_excluded']

    # -------------------------------------------------------------------------
    # COMPUTE — DURATION & DATES
    # -------------------------------------------------------------------------

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        labels = dict(self._fields['duration_unit'].selection)
        for line in self:
            line.duration_string = f"{line.duration} {labels.get(line.duration_unit, '')}"

    @api.depends('order_id', 'order_id.start_date')
    def _compute_start_date(self):
        for line in self:
            line.start_date = line.order_id.start_date

    def _inverse_start_date(self):
        pass

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

    def _inverse_end_date(self):
        pass

    @api.depends('start_date')
    def _compute_scheduled_date(self):
        for line in self:
            line.scheduled_date = (
                fields.Datetime.to_datetime(line.start_date)
                if line.start_date else False)

    # -------------------------------------------------------------------------
    # COMPUTE — STOCK
    # -------------------------------------------------------------------------

    @api.depends('order_id.warehouse_id', 'company_id')
    def _compute_warehouse_id(self):
        for line in self:
            if hasattr(line.order_id, 'warehouse_id') and line.order_id.warehouse_id:
                line.warehouse_id = line.order_id.warehouse_id
            elif line.company_id:
                line.warehouse_id = self.env['stock.warehouse'].search(
                    [('company_id', '=', line.company_id.id)], limit=1)
            else:
                line.warehouse_id = False

    @api.depends('product_id', 'src_location_id')
    def _compute_available_qty(self):
        for line in self:
            line.available_qty = 0.0
            if line.product_id and line.src_location_id:
                self._cr.execute("""
                    SELECT COALESCE(SUM(quantity), 0)
                    FROM stock_quant
                    WHERE location_id = %s AND product_id = %s
                """, (line.src_location_id.id, line.product_id.id))
                result = self._cr.fetchone()
                line.available_qty = result[0] if result else 0.0

    @api.depends('product_id')
    def _compute_available_src_location(self):
        for line in self:
            if not line.product_id:
                line.available_src_location_txt = '-'
                line.available_src_location_ids = False
                continue

            self._cr.execute("""
                SELECT loc.id, COALESCE(SUM(quant.quantity), 0) AS qty
                FROM stock_quant AS quant
                JOIN stock_location AS loc ON quant.location_id = loc.id
                WHERE loc.usage = 'internal'
                  AND quant.quantity != 0.0
                  AND quant.product_id = %s
                GROUP BY loc.id
            """, (line.product_id.id,))
            rows = self._cr.dictfetchall()

            if rows:
                texts = []
                loc_ids = []
                for row in rows:
                    loc = self.env['stock.location'].browse(row['id'])
                    loc_ids.append(loc.id)
                    texts.append(f"{loc.display_name} ({row['qty']})")
                line.available_src_location_txt = '\n'.join(texts)
                line.available_src_location_ids = self.env['stock.location'].browse(loc_ids)
            else:
                line.available_src_location_txt = 'N/A'
                line.available_src_location_ids = False

    @api.depends('product_id')
    def _compute_is_mto(self):
        mto_route = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
        for line in self:
            if line.product_id and line.product_type == 'product' and mto_route:
                line.is_mto = mto_route in line.product_id.route_ids
            else:
                line.is_mto = False

    @api.depends('product_uom_qty', 'qty_available_today')
    def _compute_qty_to_deliver(self):
        for line in self:
            line.qty_to_delivery = max(
                0, line.product_uom_qty - line.qty_available_today)

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
                    if line.virtual_available_at_date < line.product_uom_qty:
                        line.forecast_expected_date = line._get_forecast_expected_date()
                    else:
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

    def _get_forecast_expected_date(self):
        self.ensure_one()
        if not self.product_id:
            return False
        moves = self.env['stock.move'].search([
            ('product_id', '=', self.product_id.id),
            ('state', 'not in', ['done', 'cancel']),
            ('date', '>', fields.Datetime.now()),
            ('location_dest_id.usage', '=', 'internal'),
        ], order='date', limit=1)
        return moves.date if moves else False

    # -------------------------------------------------------------------------
    # DEFAULT GET
    # -------------------------------------------------------------------------

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('default_order_id'):
            order = self.env['gdi.rental.order'].browse(
                self._context['default_order_id'])
            if order:
                res.update({
                    'duration': order.duration,
                    'duration_unit': order.duration_unit,
                    'start_date': order.start_date,
                })
        return res

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    def _get_rental_pricing_list(self, product):
        if not product or not product.rental_pricing_ids:
            return False
        return {p.unit: p.price for p in product.rental_pricing_ids}

    def _compute_rental_price(self):
        """Compute price_unit based on rental pricing. NEVER touches qty."""
        if not self.product_id:
            return

        product = self.product_id.with_context(
            partner=self.order_id.partner_id,
            quantity=self.product_uom_qty,
            date=self.order_id.date_order,
            pricelist=self.order_id.pricelist_id.id,
            uom=self.product_uom.id if self.product_uom else False)

        if not (self.order_id.pricelist_id and self.order_id.partner_id):
            return

        pricing = self._get_rental_pricing_list(product)
        if not pricing:
            raise ValidationError(
                _("Rental price for duration '%s' is not configured for this "
                  "product.") % self.duration_unit)
        if self.duration_unit not in pricing:
            available = ', '.join(pricing.keys())
            raise ValidationError(
                _("This product is not available for rental by %s. "
                  "Available: %s.") % (self.duration_unit, available))

        rental_price = pricing[self.duration_unit] * self.duration
        self.price_unit = product._get_tax_included_unit_price(
            self.company_id,
            self.order_id.currency_id,
            self.order_id.date_order,
            'sale',
            fiscal_position=self.order_id.fiscal_position_id,
            product_price_unit=rental_price,
            product_currency=self.order_id.currency_id)

    # -------------------------------------------------------------------------
    # CRUD (debug logging)
    # -------------------------------------------------------------------------

    @api.model_create_multi
    def create(self, vals_list):
        for i, vals in enumerate(vals_list):
            _logger.info("=" * 60)
            _logger.info("RO LINE CREATE - Line %d", i)
            _logger.info("  product_uom_qty: %s",
                         vals.get('product_uom_qty', '*** NOT IN VALS ***'))
            _logger.info("  product_uom: %s",
                         vals.get('product_uom', '*** NOT IN VALS ***'))
            _logger.info("=" * 60)
        return super().create(vals_list)

    def write(self, vals):
        if 'product_uom_qty' in vals:
            _logger.info("RO LINE WRITE - qty=%s for IDs=%s",
                         vals['product_uom_qty'], self.ids)
        return super().write(vals)

    # -------------------------------------------------------------------------
    # ONCHANGES
    #
    # Same rules as quotation line:
    #   1. NEVER set product_uom_qty
    #   2. NEVER use self.update(dict)
    #   3. ONE onchange per trigger field
    # -------------------------------------------------------------------------

    @api.onchange('item_type')
    def _onchange_item_type(self):
        if self.item_type == 'set':
            self.product_id = False
            self.product_uom_txt = 'SET'
        elif self.item_type == 'unit':
            if self.component_line_ids:
                self.component_line_ids = [(5, 0, 0)]
            self.product_uom_txt = ''

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """Set description, UOM, and price. NEVER touches qty."""
        if not self.product_id:
            self.product_uom = False
            self.product_uom_txt = 'SET' if self.item_type == 'set' else ''
            self.price_unit = 0.0
            self.name = False
            return

        lang = get_lang(self.env, self.order_id.partner_id.lang).code
        product = self.product_id.with_context(lang=lang)
        self.name = product.display_name

        if (not self.product_uom
                or self.product_uom.category_id != self.product_id.uom_id.category_id):
            self.product_uom = self.product_id.uom_id
            self.product_uom_txt = self.product_id.uom_id.name

        self._compute_rental_price()

    @api.onchange('duration', 'duration_unit')
    def _onchange_duration(self):
        if self.product_id:
            self._compute_rental_price()

    @api.onchange('component_line_ids')
    def _onchange_component_line_ids(self):
        if self.item_type == 'set':
            self.price_unit = sum(
                c.price_subtotal for c in self.component_line_ids)

    # -------------------------------------------------------------------------
    # BUSINESS METHODS
    # -------------------------------------------------------------------------

    def check_rental_period(self):
        for line in self:
            if not line.start_date:
                raise ValidationError(
                    _("Start date for item '%s' is not defined.") % line.item_code)
            if not line.end_date:
                raise ValidationError(
                    _("End date for item '%s' is not defined.") % line.item_code)

    def _get_contract_line_vals(self):
        """Prepare contract line values from this order line."""
        self.ensure_one()
        vals = {
            'name': self.name or '',
            'item_code': self.item_code or '',
            'sequence': self.sequence or 10,
            'product_id': self.product_id.id or False,
            'product_template_id': self.product_template_id.id or False,
            'product_uom_qty': self.product_uom_qty,
            'product_uom': self.product_uom.id or False,
            'product_uom_category_id': self.product_uom_category_id.id or False,
            'product_uom_txt': self.product_uom_txt or 'SET',
            'price_unit': self.price_unit,
            'item_type': self.item_type,
            'start_date': self.start_date or False,
            'end_date': self.end_date or False,
            'duration': self.duration,
            'duration_unit': self.duration_unit,
            'ro_line_id': self.id,
        }
        if self.item_type == 'set' and self.component_line_ids:
            vals['component_line_ids'] = [
                (0, 0, {
                    'product_id': c.product_id.id,
                    'name': c.name or '',
                    'price_unit': c.price_unit,
                    'product_uom_qty': c.product_uom_qty,
                    'product_uom': c.product_uom.id,
                }) for c in self.component_line_ids
            ]
        return vals

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_view_stock_forecast(self):
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

    def action_item_hireoff(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rental.item.hireoff.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref(
                'gdi_rental.view_rental_item_hireoff_wizard_form').id,
            'target': 'new',
            'context': {'default_rental_orderline_id': self.id},
        }