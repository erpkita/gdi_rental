# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class GDIRentalSet(models.Model):
    _name = "gdi.rental.set"
    _description = "GDI Rental Set"
    _order = "name"

    name = fields.Char(string="Set Name", required=True)
    code = fields.Char(string="Item Code / Reference", required=True)
    active = fields.Boolean(default=True)
    description = fields.Text(string="Description")

    line_ids = fields.One2many(
        'gdi.rental.set.line', 'set_id', string="Components")


class GDIRentalSetLine(models.Model):
    _name = "gdi.rental.set.line"
    _description = "GDI Rental Set Component"

    set_id = fields.Many2one(
        'gdi.rental.set', string="Rental Set",
        required=True, ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string="Product", required=True,
        domain="[('rent_ok', '=', True)]")
    quantity = fields.Float(string="Quantity", default=1.0)

    # --- Stock Availability ---
    qty_available = fields.Float(
        string="On Hand",
        compute='_compute_stock_quantities',
        digits='Product Unit of Measure')
    virtual_available = fields.Float(
        string="Forecasted",
        compute='_compute_stock_quantities',
        digits='Product Unit of Measure')
    stock_status = fields.Selection([
        ('in_stock', 'In Stock'),
        ('low_stock', 'Low Stock'),
        ('out_of_stock', 'Out of Stock'),
        ('no_product', 'No Product'),
    ], string='Status', compute='_compute_stock_quantities')

    @api.depends('product_id', 'quantity')
    def _compute_stock_quantities(self):
        for line in self:
            if not line.product_id:
                line.qty_available = 0.0
                line.virtual_available = 0.0
                line.stock_status = 'no_product'
                continue

            line.qty_available = line.product_id.qty_available
            line.virtual_available = line.product_id.virtual_available

            if line.qty_available <= 0:
                line.stock_status = 'out_of_stock'
            elif line.qty_available < line.quantity:
                line.stock_status = 'low_stock'
            else:
                line.stock_status = 'in_stock'
