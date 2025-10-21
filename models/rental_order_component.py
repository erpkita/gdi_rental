# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools.misc import get_lang

class RentalQuotationComponent(models.Model):
    _name = "rental.quotation.component"
    _description = "Rental Quotation Components"


    quotation_line_id = fields.Many2one("rental.quotation.line", string="Quotation Item Ref.")
    product_id = fields.Many2one("product.product", required=True, string="Product", domain=[('rent_ok', '=', True), ('detailed_type', '=', 'product')])
    name = fields.Text(string='Description', required=True)
    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom_category_id = fields.Many2one('uom.category', related='product_id.uom_id.category_id', string="Uom Categ")
    product_uom = fields.Many2one('uom.uom', 
                                  string='Unit of Measure', 
                                  domain="[('category_id', '=', product_uom_category_id)]", 
                                  ondelete="restrict")
    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ongoing', 'Consumed'),
        ('returned', 'Returned')
    ], string="Status", default="draft")

    available_qty = fields.Float(string="Available Qty", compute="_get_available_qty")
    src_location_id = fields.Many2one("stock.location", string="Source Location")
    available_src_location_ids = fields.Many2many("stock.location", string="Src Location Ids", compute="_get_available_src_location")
    available_src_location_txt = fields.Text("Available Src Location", compute="_get_available_src_location")
    lot_id = fields.Many2one("stock.production.lot", domain=[('product_id', '=', product_id)])

    quotation_duration = fields.Integer(string="Duration", related="quotation_line_id.duration")
    quotation_duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", related="quotation_line_id.duration_unit")

    price_subtotal = fields.Monetary(string="Subtotal", store=True, compute="_compute_amount")
    currency_id = fields.Many2one(related='quotation_line_id.currency_id', depends=['quotation_line_id.currency_id'], store=True, string='Currency')

    # Stock visibility fields
    product_type = fields.Selection(related='product_id.type', string="Product Type")
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        compute='_compute_warehouse_id',
        store=True
    )
    
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

    @api.depends('quotation_line_id.warehouse_id', 'quotation_line_id.company_id')
    def _compute_warehouse_id(self):
        """Get warehouse from quotation line."""
        for component in self:
            if component.quotation_line_id and component.quotation_line_id.warehouse_id:
                component.warehouse_id = component.quotation_line_id.warehouse_id
            elif component.quotation_line_id and component.quotation_line_id.company_id:
                warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', component.quotation_line_id.company_id.id)
                ], limit=1)
                component.warehouse_id = warehouse
            else:
                component.warehouse_id = False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        """Compute current and forecast stock quantities with status"""
        for component in self:
            if not component.product_id:
                component.current_stock_qty = 0.0
                component.virtual_stock_qty = 0.0
                component.stock_status = 'no_product'
                component.stock_info_display = 'No Product Selected'
                continue
                
            # Get warehouse context
            warehouse_id = component.warehouse_id.id if component.warehouse_id else component.env.user.company_id.warehouse_id.id
            
            # Get stock quantities with warehouse context
            product_with_context = component.product_id.with_context(warehouse=warehouse_id)
            
            component.current_stock_qty = product_with_context.qty_available
            component.virtual_stock_qty = product_with_context.virtual_available
            
            # Determine stock status
            if component.current_stock_qty > 0:
                if component.current_stock_qty >= component.product_uom_qty:
                    component.stock_status = 'in_stock'
                else:
                    component.stock_status = 'low_stock'
            else:
                component.stock_status = 'out_of_stock'
            
            # Create display string
            component.stock_info_display = f"Available: {component.current_stock_qty:.0f} | Forecast: {component.virtual_stock_qty:.0f}"

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

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (0.0) / 100.0)
            line.update({
                'price_subtotal': price * line.product_uom_qty,
            })
    
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

    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return
        self.update({'name': self.product_id.display_name})
        vals = {}
        if not self.product_uom or (self.product_id.uom_id.id != self.product_uom.id):
            rental_pricing_list = self._get_rental_pricing_list(self.product_id)
            if not rental_pricing_list:
                raise ValidationError(
                    "Rental price for the selected duration (%s) is not configured for this product. Please contact the administrator or choose different duration." % (self.quotation_duration_unit)
                )
            rental_pricing_keys = rental_pricing_list.keys()
            if self.quotation_duration_unit not in rental_pricing_keys:
                readable_units = ", ".join(rental_pricing_keys)
                raise ValidationError(
                    f"This product is not available for rental by {self.quotation_duration_unit}. "
                    f"Please choose from the available options: {readable_units}."
                )
            rental_price = rental_pricing_list[self.quotation_duration_unit] * self.quotation_duration
            vals['product_uom'] = self.product_id.uom_id
            vals['price_unit'] = rental_price
            vals['product_uom_qty'] = self.product_uom_qty or 1.0
        vals['src_location_id'] = False
        self.update(vals)

    def _get_rental_pricing_list(self, product):
        if not product or not product.rental_pricing_ids:
            return False
        
        return {price.unit: price.price for price in product.rental_pricing_ids}
    

class RentalOrderComponent(models.Model):
    _name = "rental.order.component"
    _description = "Rental Order Components"


    order_line_id = fields.Many2one("gdi.rental.order.line", string="Order Item Ref.")
    product_id = fields.Many2one("product.product", required=True, string="Product", domain=[('rent_ok', '=', True), ('detailed_type', '=', 'product')])
    name = fields.Text(string='Description', required=True)
    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom_category_id = fields.Many2one('uom.category', related='product_id.uom_id.category_id', string="Uom Categ")
    product_uom = fields.Many2one('uom.uom', 
                                  string='Unit of Measure', 
                                  domain="[('category_id', '=', product_uom_category_id)]", 
                                  ondelete="restrict")
    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ongoing', 'Consumed'),
        ('returned', 'Returned')
    ], string="Status", default="draft")

    available_qty = fields.Float(string="Available Qty", compute="_get_available_qty")
    src_location_id = fields.Many2one("stock.location", string="Source Location")
    available_src_location_ids = fields.Many2many("stock.location", string="Src Location Ids", compute="_get_available_src_location")
    available_src_location_txt = fields.Text("Available Src Location", compute="_get_available_src_location")
    lot_id = fields.Many2one("stock.production.lot", domain=[('product_id', '=', product_id)])

    duration = fields.Integer(string="Duration", related="order_line_id.duration")
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", related="order_line_id.duration_unit")

    price_subtotal = fields.Monetary(string="Subtotal", store=True, compute="_compute_amount")
    currency_id = fields.Many2one(related='order_line_id.currency_id', depends=['order_line_id.currency_id'], store=True, string='Currency')

    # Stock visibility fields
    product_type = fields.Selection(related='product_id.type', string="Product Type")
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        compute='_compute_warehouse_id',
        store=True
    )
    
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

    stock_move_ids = fields.One2many("stock.move", "rental_order_component_id", string="Stock Moves")

    @api.depends('order_line_id.warehouse_id', 'order_line_id.company_id')
    def _compute_warehouse_id(self):
        """Get warehouse from order line."""
        for component in self:
            if component.order_line_id and component.order_line_id.warehouse_id:
                component.warehouse_id = component.order_line_id.warehouse_id
            elif component.order_line_id and component.order_line_id.company_id:
                warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', component.order_line_id.company_id.id)
                ], limit=1)
                component.warehouse_id = warehouse
            else:
                component.warehouse_id = False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        """Compute current and forecast stock quantities with status"""
        for component in self:
            if not component.product_id:
                component.current_stock_qty = 0.0
                component.virtual_stock_qty = 0.0
                component.stock_status = 'no_product'
                component.stock_info_display = 'No Product Selected'
                continue
                
            # Get warehouse context
            warehouse_id = component.warehouse_id.id if component.warehouse_id else component.env.user.company_id.warehouse_id.id
            
            # Get stock quantities with warehouse context
            product_with_context = component.product_id.with_context(warehouse=warehouse_id)
            
            component.current_stock_qty = product_with_context.qty_available
            component.virtual_stock_qty = product_with_context.virtual_available
            
            # Determine stock status
            if component.current_stock_qty > 0:
                if component.current_stock_qty >= component.product_uom_qty:
                    component.stock_status = 'in_stock'
                else:
                    component.stock_status = 'low_stock'
            else:
                component.stock_status = 'out_of_stock'
            
            # Create display string
            component.stock_info_display = f"Available: {component.current_stock_qty:.0f} | Forecast: {component.virtual_stock_qty:.0f}"

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

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (0.0) / 100.0)
            line.update({
                'price_subtotal': price * line.product_uom_qty,
            })
    
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

    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return
        self.update({'name': self.product_id.display_name})
        vals = {}
        if not self.product_uom or (self.product_id.uom_id.id != self.product_uom.id):
            rental_pricing_list = self._get_rental_pricing_list(self.product_id)
            rental_price = rental_pricing_list[self.duration_unit] * self.duration
            vals['product_uom'] = self.product_id.uom_id
            vals['price_unit'] = rental_price
            vals['product_uom_qty'] = self.product_uom_qty or 1.0
        vals['src_location_id'] = False
        self.update(vals)

    def _get_rental_pricing_list(self, product):
        if not product or not product.rental_pricing_ids:
            return False
        
        return {price.unit: price.price for price in product.rental_pricing_ids}
    
class RentalContractComponent(models.Model):
    _name = "rental.contract.component"
    _description = "Rental Contract Components"


    contract_line_id = fields.Many2one("rental.contract.line", string="Contract Item Ref.")
    product_id = fields.Many2one("product.product", required=True, string="Product", domain=[('rent_ok', '=', True), ('detailed_type', '=', 'product')])
    name = fields.Text(string='Description', required=True)
    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom_category_id = fields.Many2one('uom.category', related='product_id.uom_id.category_id', string="Uom Categ")
    product_uom = fields.Many2one('uom.uom', 
                                  string='Unit of Measure', 
                                  domain="[('category_id', '=', product_uom_category_id)]", 
                                  ondelete="restrict")
    price_unit = fields.Float('Unit Price', required=True, digits='Product Price', default=0.0)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('ongoing', 'Consumed'),
        ('returned', 'Returned')
    ], string="Status", default="draft")

    available_qty = fields.Float(string="Available Qty", compute="_get_available_qty")
    src_location_id = fields.Many2one("stock.location", string="Source Location")
    available_src_location_ids = fields.Many2many("stock.location", string="Src Location Ids", compute="_get_available_src_location")
    available_src_location_txt = fields.Text("Available Src Location", compute="_get_available_src_location")
    lot_id = fields.Many2one("stock.production.lot", domain=[('product_id', '=', product_id)])

    duration = fields.Integer(string="Duration", related="contract_line_id.duration")
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", related="contract_line_id.duration_unit")

    price_subtotal = fields.Monetary(string="Subtotal", store=True, compute="_compute_amount")
    currency_id = fields.Many2one(related='contract_line_id.currency_id', depends=['contract_line_id.currency_id'], store=True, string='Currency')

    # Stock visibility fields
    product_type = fields.Selection(related='product_id.type', string="Product Type")
    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        compute='_compute_warehouse_id',
        store=True
    )
    
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

    @api.depends('contract_line_id.warehouse_id', 'contract_line_id.company_id')
    def _compute_warehouse_id(self):
        """Get warehouse from contract line."""
        for component in self:
            if component.contract_line_id and component.contract_line_id.warehouse_id:
                component.warehouse_id = component.contract_line_id.warehouse_id
            elif component.contract_line_id and component.contract_line_id.company_id:
                warehouse = self.env['stock.warehouse'].search([
                    ('company_id', '=', component.contract_line_id.company_id.id)
                ], limit=1)
                component.warehouse_id = warehouse
            else:
                component.warehouse_id = False

    @api.depends('product_id', 'warehouse_id')
    def _compute_stock_quantities(self):
        """Compute current and forecast stock quantities with status"""
        for component in self:
            if not component.product_id:
                component.current_stock_qty = 0.0
                component.virtual_stock_qty = 0.0
                component.stock_status = 'no_product'
                component.stock_info_display = 'No Product Selected'
                continue
                
            # Get warehouse context
            warehouse_id = component.warehouse_id.id if component.warehouse_id else component.env.user.company_id.warehouse_id.id
            
            # Get stock quantities with warehouse context
            product_with_context = component.product_id.with_context(warehouse=warehouse_id)
            
            component.current_stock_qty = product_with_context.qty_available
            component.virtual_stock_qty = product_with_context.virtual_available
            
            # Determine stock status
            if component.current_stock_qty > 0:
                if component.current_stock_qty >= component.product_uom_qty:
                    component.stock_status = 'in_stock'
                else:
                    component.stock_status = 'low_stock'
            else:
                component.stock_status = 'out_of_stock'
            
            # Create display string
            component.stock_info_display = f"Available: {component.current_stock_qty:.0f} | Forecast: {component.virtual_stock_qty:.0f}"

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

    @api.depends('product_uom_qty', 'price_unit')
    def _compute_amount(self):
        """
        Compute the amounts of the SO line.
        """
        for line in self:
            price = line.price_unit * (1 - (0.0) / 100.0)
            line.update({
                'price_subtotal': price * line.product_uom_qty,
            })
    
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

    @api.onchange('product_id')
    def product_id_change(self):
        if not self.product_id:
            return
        self.update({'name': self.product_id.display_name})
        vals = {}
        if not self.product_uom or (self.product_id.uom_id.id != self.product_uom.id):
            rental_pricing_list = self._get_rental_pricing_list(self.product_id)
            rental_price = rental_pricing_list[self.duration_unit] * self.duration
            vals['product_uom'] = self.product_id.uom_id
            vals['price_unit'] = rental_price
            vals['product_uom_qty'] = self.product_uom_qty or 1.0
        vals['src_location_id'] = False
        self.update(vals)

    def _get_rental_pricing_list(self, product):
        if not product or not product.rental_pricing_ids:
            return False
        
        return {price.unit: price.price for price in product.rental_pricing_ids}