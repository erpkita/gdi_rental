# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _
from odoo.tools.misc import get_lang

class RentalOrderComponent(models.Model):
    _name = "rental.quotation.component"
    _description = "Rental Order Components"


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
    
class RentalOrderComponent(models.Model):
    _name = "rental.contract.component"
    _description = "Rental Contract Components"


    contract_line_id = fields.Many2one("rental.contract.line", string="Order Item Ref.")
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