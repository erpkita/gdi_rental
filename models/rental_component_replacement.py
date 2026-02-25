# -*- coding: utf-8 -*-

from odoo import api, fields, models, _


class RentalComponentReplacement(models.Model):
    _name = "rental.component.replacement"
    _description = "Rental Component Replacement Log"
    _order = "date desc, id desc"

    name = fields.Char(
        string="Reference", required=True, copy=False,
        readonly=True, default=lambda self: _('New'))
    order_line_id = fields.Many2one(
        "gdi.rental.order.line", string="Rental Order Line",
        required=True, ondelete="cascade", index=True)
    order_id = fields.Many2one(
        "gdi.rental.order", string="Rental Order",
        related="order_line_id.order_id", store=True)

    # Component links
    old_component_id = fields.Many2one(
        "rental.order.component", string="Old Component",
        required=True, ondelete="restrict")
    new_component_id = fields.Many2one(
        "rental.order.component", string="New Component",
        ondelete="restrict")

    # Lot tracking
    old_lot_id = fields.Many2one(
        "stock.production.lot", string="Old Serial/Lot")
    new_lot_id = fields.Many2one(
        "stock.production.lot", string="New Serial/Lot")
    product_id = fields.Many2one(
        "product.product", string="Product",
        related="old_component_id.product_id", store=True)

    # Reason
    reason = fields.Selection([
        ('defective', 'Defective'),
        ('damaged', 'Damaged'),
        ('upgrade', 'Upgrade'),
        ('customer_request', 'Customer Request'),
        ('other', 'Other'),
    ], string="Reason", required=True)
    notes = fields.Text(string="Notes")

    # Old item destination
    old_item_destination = fields.Selection([
        ('warehouse', 'Return to Warehouse'),
        ('scrap', 'Scrap'),
        ('repair', 'Send to Repair'),
    ], string="Old Item Destination", required=True)
    dest_location_id = fields.Many2one(
        "stock.location", string="Destination Location",
        help="Exact location where the old item was sent.")
    src_location_id = fields.Many2one(
        "stock.location", string="Source Location",
        help="Location the new item was picked from.")

    # Picking references
    return_picking_id = fields.Many2one(
        "stock.picking", string="Return Transfer",
        help="Transfer that returned the old item.")
    delivery_picking_id = fields.Many2one(
        "stock.picking", string="Delivery Transfer",
        help="Transfer that delivered the new item.")

    date = fields.Datetime(
        string="Date", default=fields.Datetime.now, required=True)
    user_id = fields.Many2one(
        "res.users", string="Performed By",
        default=lambda self: self.env.user, required=True)

    state = fields.Selection([
        ('draft', 'Draft'),
        ('done', 'Done'),
        ('cancel', 'Cancelled'),
    ], string="Status", default='draft')

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'rental.component.replacement.seq') or _('New')
        return super().create(vals)
