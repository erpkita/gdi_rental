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
    
    product_type = fields.Selection(related='product_id.type', string="Product Type")
    virtual_available_at_date = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure',
        string='Forecast Quantity',
    )
    qty_available_today = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure',
        string='Available Today',
    )
    free_qty_today = fields.Float(
        compute='_compute_qty_at_date',
        digits='Product Unit of Measure',
        string='Free Quantity Today',
    )
    scheduled_date = fields.Datetime(
        string='Scheduled Date',
        compute='_compute_scheduled_date',
        store=True
    )
    forecast_expected_date = fields.Datetime(
        compute='_compute_qty_at_date',
        string='Expected Date'
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        compute='_compute_warehouse_id',
        store=True,
        check_company=True
    )

    is_mto = fields.Boolean(
        string="Is Made to Order",
        compute="_compute_is_mto",
        store=False
    )

    qty_to_delivery = fields.Float(
        string="Quantity to Deliver",
        compute="_compute_qty_to_deliver",
        digits='Product Unit of Measure',
        store=False
    )

    # Enhanced stock visibility fields
    current_stock_qty = fields.Float(
        string='Current Stock',
        compute='_compute_stock_quantities',
        digits='Product Unit of Measure',
        help='Current available quantity in warehouse'
    )

    virtual_stock_qty = fields.Float(
        string='Forecast Stock',
        compute='_compute_stock_quantities', 
        digits='Product Unit of Measure',
        help='Forecasted quantity (current + incoming - outgoing)'
    )

    stock_status = fields.Selection([
        ('in_stock', 'In Stock'),
        ('low_stock', 'Low Stock'),
        ('out_of_stock', 'Out of Stock'),
        ('no_product', 'No Product Selected')
    ], string='Stock Status', compute='_compute_stock_quantities')

    stock_info_display = fields.Char(
        string='Stock Info',
        compute='_compute_stock_quantities',
        help='Quick stock information display'
    )

    # Add these compute methods to your RentalContractLine class

    @api.depends('product_id')
    def _compute_is_mto(self):
        """Check if product is Make to Order."""
        for line in self:
            if line.product_id and line.product_type == 'product':
                # Check if product has MTO route
                mto_route = self.env.ref('stock.route_warehouse0_mto', raise_if_not_found=False)
                if mto_route:
                    line.is_mto = mto_route in line.product_id.route_ids
                else:
                    line.is_mto = False
            else:
                line.is_mto = False

    @api.depends('product_uom_qty', 'qty_available_today')
    def _compute_qty_to_deliver(self):
        """Compute quantity that needs to be delivered."""
        for line in self:
            line.qty_to_delivery = max(0, line.product_uom_qty - line.qty_available_today)

    @api.depends('product_id', 'product_uom_qty', 'start_date', 'warehouse_id', 'product_type')
    def _compute_qty_at_date(self):
        """Compute forecast quantities for the product at the scheduled date."""
        for line in self:
            if not line.product_id or line.product_type != 'product':
                line.virtual_available_at_date = 0.0
                line.qty_available_today = 0.0
                line.free_qty_today = 0.0
                line.forecast_expected_date = False
                continue

            try:
                # Get warehouse context
                warehouse = line.warehouse_id.id if line.warehouse_id else False
                
                # Get current quantities
                product_ctx = line.product_id.with_context(warehouse=warehouse)
                line.qty_available_today = product_ctx.qty_available or 0.0
                line.free_qty_today = product_ctx.free_qty or 0.0

                # Get forecast at scheduled date
                if line.scheduled_date:
                    product_forecast = line.product_id.with_context(
                        warehouse=warehouse,
                        to_date=line.scheduled_date
                    )
                    line.virtual_available_at_date = product_forecast.virtual_available or 0.0
                    
                    # Calculate expected date if quantity is insufficient
                    if line.virtual_available_at_date < line.product_uom_qty:
                        line.forecast_expected_date = line._get_forecast_expected_date()
                    else:
                        line.forecast_expected_date = False
                else:
                    line.virtual_available_at_date = line.free_qty_today
                    line.forecast_expected_date = False
                    
            except Exception as e:
                # Fallback to safe defaults if there's any error
                line.virtual_available_at_date = 0.0
                line.qty_available_today = 0.0
                line.free_qty_today = 0.0
                line.forecast_expected_date = False

    @api.depends('start_date')
    def _compute_scheduled_date(self):
        """Compute scheduled date based on start date."""
        for line in self:
            if line.start_date:
                # Convert date to datetime (start of day in UTC)
                line.scheduled_date = fields.Datetime.to_datetime(line.start_date)
            else:
                line.scheduled_date = False

    @api.depends('contract_id.warehouse_id', 'company_id')
    def _compute_warehouse_id(self):
        """Get warehouse from contract or company default."""
        for line in self:
            if hasattr(line.contract_id, 'warehouse_id') and line.contract_id.warehouse_id:
                line.warehouse_id = line.contract_id.warehouse_id
            elif line.company_id:
                warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', line.company_id.id)
                ], limit=1)
                line.warehouse_id = warehouse
            else:
                line.warehouse_id = False

    def _get_forecast_expected_date(self):
        """Calculate the expected date when stock will be available."""
        self.ensure_one()
        if not self.product_id:
            return False
            
        # Look for incoming stock moves
        moves = self.env['stock.move'].search([
            ('product_id', '=', self.product_id.id),
            ('state', 'not in', ['done', 'cancel']),
            ('date', '>', fields.Datetime.now()),
            ('location_dest_id.usage', '=', 'internal')
        ], order='date', limit=1)
        
        return moves.date if moves else False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        """Compute current and forecast stock quantities with status"""
        for line in self:
            if not line.product_id:
                line.current_stock_qty = 0.0
                line.virtual_stock_qty = 0.0
                line.stock_status = 'no_product'
                line.stock_info_display = 'No Product Selected'
                continue
                
            # Get warehouse context
            warehouse_id = line.warehouse_id.id if line.warehouse_id else line.env.user.company_id.warehouse_id.id
            
            # Get stock quantities with warehouse context
            product_with_context = line.product_id.with_context(warehouse=warehouse_id)
            
            line.current_stock_qty = product_with_context.qty_available
            line.virtual_stock_qty = product_with_context.virtual_available
            
            # Determine stock status
            if line.current_stock_qty > 0:
                if line.current_stock_qty >= line.product_uom_qty:
                    line.stock_status = 'in_stock'
                else:
                    line.stock_status = 'low_stock'
            else:
                line.stock_status = 'out_of_stock'
            
            # Create display string
            line.stock_info_display = f"Available: {line.current_stock_qty:.0f} | Forecast: {line.virtual_stock_qty:.0f}"

    @api.onchange('product_id')
    def _onchange_product_stock_info(self):
        """Trigger stock computation when product is selected - no popup warnings"""
        # Just trigger recomputation of stock fields - the widget will show the inline info
        pass

    def action_view_stock_forecast(self):
        """Open the stock forecast (replenishment) page for the selected product"""
        self.ensure_one()
        if not self.product_id:
            return False

        # Get warehouse
        warehouse_id = self.warehouse_id.id if self.warehouse_id else self.env.user.company_id.warehouse_id.id

        # Get the existing stock replenishment action
        action = self.env.ref('stock.stock_replenishment_product_product_action').read()[0]

        # Customize the domain and context
        action.update({
            'name': f'Stock Forecast - {self.product_id.display_name}',
            'domain': [('product_id', '=', self.product_id.id)],
            'context': {
                'search_default_product_id': self.product_id.id,
                'default_product_id': self.product_id.id,
                'default_warehouse_id': warehouse_id,
                'search_default_warehouse_id': warehouse_id,
            },
        })

        return action
    
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