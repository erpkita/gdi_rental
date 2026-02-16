# -*- coding: utf-8 -*-

from odoo import api, fields, models, _

class GDIRentalSet(models.Model):
    _name = "gdi.rental.set"
    _description = "GDI Rental Set"
    _order = "name"

    name = fields.Char(string="Set Name", required=True)
    code = fields.Char(string="Item Code / Reference", required=True)
    active = fields.Boolean(default=True)
    
    line_ids = fields.One2many(
        'gdi.rental.set.line', 'set_id', string="Components")

class GDIRentalSetLine(models.Model):
    _name = "gdi.rental.set.line"
    _description = "GDI Rental Set Component"

    set_id = fields.Many2one('gdi.rental.set', string="Rental Set", required=True, ondelete='cascade')
    product_id = fields.Many2one(
        'product.product', string="Product", required=True,
        domain="[('rent_ok', '=', True)]")
    quantity = fields.Float(string="Quantity", default=1.0)
