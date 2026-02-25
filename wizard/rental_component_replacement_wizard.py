# -*- coding: utf-8 -*-

import logging

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)


class RentalComponentReplacementWizard(models.TransientModel):
    _name = "rental.component.replacement.wizard"
    _description = "Rental Component Replacement Wizard"

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        component_id = self._context.get('default_component_id')
        if component_id:
            component = self.env["rental.order.component"].browse(component_id)
            if not component.exists():
                raise ValidationError(
                    _("The component you are trying to replace no longer exists."))
            res.update({
                'component_id': component.id,
                'new_product_id': component.product_id.id,
            })

        return res

    # --- Old component info (readonly) ---
    component_id = fields.Many2one(
        "rental.order.component", string="Component to Replace",
        required=True, readonly=True)
    product_id = fields.Many2one(
        "product.product", string="Product",
        related="component_id.product_id", readonly=True)
    current_lot_id = fields.Many2one(
        "stock.production.lot", string="Current Serial/Lot",
        related="component_id.lot_id", readonly=True)
    order_line_id = fields.Many2one(
        "gdi.rental.order.line", string="Rental Order Line",
        related="component_id.order_line_id", readonly=True)

    # --- New item ---
    new_product_id = fields.Many2one(
        "product.product", string="Replacement Product",
        required=True,
        domain="[('rent_ok', '=', True), ('detailed_type', '=', 'product')]",
        help="Defaults to the same product. Change if upgrading.")
    new_lot_id = fields.Many2one(
        "stock.production.lot", string="New Serial/Lot",
        required=True,
        domain="[('product_id', '=', new_product_id)]")
    new_src_location_id = fields.Many2one(
        "stock.location", string="Pick From Location",
        required=True,
        domain="[('usage', '=', 'internal')]",
        help="Warehouse location to pick the replacement item from.")

    # --- Reason ---
    reason = fields.Selection([
        ('defective', 'Defective'),
        ('damaged', 'Damaged'),
        ('upgrade', 'Upgrade'),
        ('customer_request', 'Customer Request'),
        ('other', 'Other'),
    ], string="Reason", required=True)
    notes = fields.Text(string="Notes")

    # --- Old item destination ---
    old_item_destination = fields.Selection([
        ('warehouse', 'Return to Warehouse'),
        ('scrap', 'Scrap'),
        ('repair', 'Send to Repair'),
    ], string="Old Item Destination", required=True, default='warehouse')
    dest_location_id = fields.Many2one(
        "stock.location", string="Return To Location",
        required=True,
        domain="[('usage', 'in', ['internal', 'inventory'])]",
        help="Exact location where the old item will be sent.")

    @api.onchange('old_item_destination')
    def _onchange_old_item_destination(self):
        """Set default dest_location based on destination type."""
        if self.old_item_destination == 'scrap':
            scrap_loc = self.env['stock.location'].search(
                [('scrap_location', '=', True)], limit=1)
            if scrap_loc:
                self.dest_location_id = scrap_loc.id
        elif self.old_item_destination == 'warehouse':
            picking_type = self.env["stock.picking.type"].search(
                [('name', '=', 'Rental Physical Inventory')], limit=1)
            if picking_type:
                self.dest_location_id = picking_type.default_location_src_id.id

    # -------------------------------------------------------------------------
    # Main action
    # -------------------------------------------------------------------------
    def action_confirm(self):
        self.ensure_one()
        component = self.component_id
        order_line = component.order_line_id

        if component.state != 'ongoing':
            raise ValidationError(
                _("Only components in 'Ongoing' state can be replaced."))

        # 1) Find previous outbound move for this component
        prev_move = self._find_previous_outbound_move(component)
        if not prev_move:
            raise ValidationError(
                _("Cannot find the previous delivery move for component '%s'. "
                  "Please contact your system administrator.") % component.name)

        # 2) Create return picking (old item: customer → dest_location)
        return_picking = self._create_return_picking(component, prev_move)

        # 3) Create delivery picking (new item: src_location → customer)
        delivery_picking = self._create_delivery_picking(
            component, order_line, prev_move)

        # 4) Create new component record
        new_component = self._create_replacement_component(component, order_line)

        # 5) Update old component state
        component.write({
            'state': 'replaced',
            'replaced_by_component_id': new_component.id,
        })

        # 6) Create replacement log
        replacement = self.env["rental.component.replacement"].create({
            'order_line_id': order_line.id,
            'old_component_id': component.id,
            'new_component_id': new_component.id,
            'old_lot_id': component.lot_id.id or False,
            'new_lot_id': self.new_lot_id.id,
            'reason': self.reason,
            'notes': self.notes or False,
            'old_item_destination': self.old_item_destination,
            'dest_location_id': self.dest_location_id.id,
            'src_location_id': self.new_src_location_id.id,
            'return_picking_id': return_picking.id,
            'delivery_picking_id': delivery_picking.id,
            'state': 'done',
        })

        # 7) Return action to view the replacement log
        return self._action_view_replacement(replacement)

    # -------------------------------------------------------------------------
    # Previous move lookup
    # -------------------------------------------------------------------------
    def _find_previous_outbound_move(self, component):
        """
        Find the most recent outbound stock move for this component.
        Uses the component's stock_move_ids linked via
        rental_order_component_id on stock.move.
        """
        if component.stock_move_ids:
            # Get the last outbound move (done state preferred)
            done_moves = component.stock_move_ids.filtered(
                lambda m: m.state == 'done')
            if done_moves:
                return done_moves.sorted(key=lambda m: m.id, reverse=True)[0]
            return component.stock_move_ids.sorted(
                key=lambda m: m.id, reverse=True)[0]

        # Fallback: search by product in the order line's stock moves
        order_line = component.order_line_id
        if order_line and order_line.stock_move_ids:
            last_move = order_line.stock_move_ids.sorted(
                key=lambda m: m.id, reverse=True)[0]
            picking = last_move.picking_id
            if picking:
                component_move = picking.move_lines.filtered(
                    lambda m: m.product_id.id == component.product_id.id)
                if component_move:
                    return component_move[0]

        return False

    # -------------------------------------------------------------------------
    # Return picking (old item back)
    # -------------------------------------------------------------------------
    def _create_return_picking(self, component, prev_move):
        """
        Create a validated return picking to bring the old component
        from the customer location to the selected destination.
        """
        picking_type = self.env["stock.picking.type"].search(
            [('name', '=', 'Rental Physical Inventory')], limit=1)
        if not picking_type:
            raise ValidationError(
                _("Picking type 'Rental Physical Inventory' not found. "
                  "Please contact your system administrator."))

        current_dt = fields.Datetime.now()
        order = component.order_line_id.order_id

        # Prepare move line with lot from previous move
        move_line_vals = self._prepare_return_move_lines(
            component, prev_move, picking_type, current_dt)

        product = component.product_id
        name = product.product_name or product.name or component.name

        move_vals = (0, 0, {
            'name': f"Return: {name}",
            'description_picking': name,
            'product_id': product.id,
            'product_uom': component.product_uom.id,
            'product_uom_qty': component.product_uom_qty,
            'date': current_dt,
            'location_id': prev_move.location_dest_id.id,  # customer location
            'location_dest_id': self.dest_location_id.id,
            'move_line_ids': move_line_vals,
        })

        picking_vals = {
            'partner_id': order.partner_id.id,
            'contact_person_id': order.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': prev_move.location_dest_id.id,  # customer
            'location_dest_id': self.dest_location_id.id,
            'move_type': 'direct',
            'scheduled_date': current_dt,
            'date_deadline': current_dt,
            'origin': f"RPL {component.name} (IN) RO: {order.name}",
            'src_user_id': self.env.user.id,
            'move_ids_without_package': [move_vals],
        }

        try:
            picking = self.env["stock.picking"].create(picking_vals)
            picking.button_validate()
            return picking
        except Exception as e:
            _logger.error("Failed to create return picking for replacement: %s", e)
            raise UserError(
                _("Failed to create return transfer. Error: %s") % str(e))

    def _prepare_return_move_lines(self, component, prev_move,
                                   picking_type, current_dt):
        """Build move_line_ids for the return move, preserving lot."""
        move_lines = []

        if prev_move.move_line_ids:
            for ml in prev_move.move_line_ids:
                move_lines.append((0, 0, {
                    'product_id': component.product_id.id,
                    'product_uom_id': component.product_uom.id,
                    'product_uom_qty': ml.qty_done,
                    'qty_done': ml.qty_done,
                    'date': current_dt,
                    'location_id': prev_move.location_dest_id.id,
                    'location_dest_id': self.dest_location_id.id,
                    'lot_id': ml.lot_id.id if ml.lot_id else False,
                }))
        else:
            # No previous move lines — create one without lot
            move_lines.append((0, 0, {
                'product_id': component.product_id.id,
                'product_uom_id': component.product_uom.id,
                'product_uom_qty': component.product_uom_qty,
                'qty_done': component.product_uom_qty,
                'date': current_dt,
                'location_id': prev_move.location_dest_id.id,
                'location_dest_id': self.dest_location_id.id,
                'lot_id': component.lot_id.id if component.lot_id else False,
            }))

        return move_lines

    # -------------------------------------------------------------------------
    # Delivery picking (new item out)
    # -------------------------------------------------------------------------
    def _create_delivery_picking(self, old_component, order_line, prev_move):
        """
        Create a validated delivery picking to send the replacement
        component from the warehouse to the customer.
        """
        picking_type = self.env["stock.picking.type"].search(
            [('name', '=', 'Rental Delivery Orders')], limit=1)
        if not picking_type:
            picking_type = self.env["stock.picking.type"].search(
                [('name', '=', 'Delivery Orders')], limit=1)
        if not picking_type:
            raise ValidationError(
                _("Delivery picking type not found. "
                  "Please contact your system administrator."))

        current_dt = fields.Datetime.now()
        order = order_line.order_id
        customer_location = prev_move.location_dest_id  # customer location

        product = self.new_product_id
        name = product.product_name or product.name

        move_line_vals = [(0, 0, {
            'product_id': product.id,
            'product_uom_id': old_component.product_uom.id,
            'product_uom_qty': old_component.product_uom_qty,
            'qty_done': old_component.product_uom_qty,
            'date': current_dt,
            'location_id': self.new_src_location_id.id,
            'location_dest_id': customer_location.id,
            'lot_id': self.new_lot_id.id,
        })]

        move_vals = (0, 0, {
            'name': f"Replace: {name}",
            'description_picking': name,
            'product_id': product.id,
            'product_uom': old_component.product_uom.id,
            'product_uom_qty': old_component.product_uom_qty,
            'date': current_dt,
            'location_id': self.new_src_location_id.id,
            'location_dest_id': customer_location.id,
            'move_line_ids': move_line_vals,
            'ro_line_id': order_line.id,
            'rental_order_component_id': False,  # will be set to new component later
        })

        picking_vals = {
            'partner_id': order.partner_id.id,
            'contact_person_id': order.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': self.new_src_location_id.id,
            'location_dest_id': customer_location.id,
            'move_type': 'direct',
            'scheduled_date': current_dt,
            'date_deadline': current_dt,
            'origin': f"RPL {old_component.name} (OUT) RO: {order.name}",
            'src_user_id': self.env.user.id,
            'gdi_rental_id': order.id,
            'move_ids_without_package': [move_vals],
        }

        try:
            picking = self.env["stock.picking"].create(picking_vals)
            picking.button_validate()
            return picking
        except Exception as e:
            _logger.error("Failed to create delivery picking for replacement: %s", e)
            raise UserError(
                _("Failed to create delivery transfer. Error: %s") % str(e))

    # -------------------------------------------------------------------------
    # New component creation
    # -------------------------------------------------------------------------
    def _create_replacement_component(self, old_component, order_line):
        """Create a new component record as the replacement."""
        return self.env["rental.order.component"].create({
            'order_line_id': order_line.id,
            'product_id': self.new_product_id.id,
            'name': self.new_product_id.display_name,
            'product_uom_qty': old_component.product_uom_qty,
            'product_uom': old_component.product_uom.id,
            'price_unit': old_component.price_unit,
            'lot_id': self.new_lot_id.id,
            'src_location_id': self.new_src_location_id.id,
            'state': 'ongoing',
            'replaces_component_id': old_component.id,
        })

    # -------------------------------------------------------------------------
    # View action
    # -------------------------------------------------------------------------
    def _action_view_replacement(self, replacement):
        """Return action to display the created replacement log."""
        return {
            'type': 'ir.actions.act_window',
            'name': _('Component Replacement'),
            'res_model': 'rental.component.replacement',
            'view_mode': 'form',
            'res_id': replacement.id,
            'target': 'current',
        }
