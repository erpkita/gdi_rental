# -*- coding: utf-8 -*-


from datetime import timedelta

from odoo import api, fields, models, _

class RentalOrderComponent(models.Model):
    _name = "rental.order.component"
    _description = "Rental Order Components"


    quotation_line_id = fields.Many2one("rental.quotation.line", string="Quotation Item Ref.")
    order_line_id = fields.Many2one("gdi.rental.order.line", string="Order Item Ref.")
    product_id = fields.Many2one("product.product", string="Product", domain=[('sale_ok', '=', True), ('detailed_type', '=', 'consu')])
    name = fields.Text(string='Description', required=True)
    product_uom_qty = fields.Float(string='Quantity', digits='Product Unit of Measure', required=True, default=1.0)
    product_uom_category_id = fields.Many2one(related='product_id.uom_id.category_id')
    product_uom = fields.Many2one('uom.uom', 
                                  string='Unit of Measure', 
                                  domain="[('category_id', '=', product_uom_category_id)]", 
                                  ondelete="restrict")

    state = fields.Selection([
        ('draft', 'Draft'),
        ('ongoing', 'Consumed'),
        ('returned', 'Returned')
    ], string="Status", default="draft")