# -*- coding: utf-8 -*-

import logging

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

_logger = logging.getLogger(__name__)

class RentalItemHireoffWizard(models.TransientModel):
    _name = "rental.item.hireoff.wizard"
    _description = "Rental Item Hire-Off Wizard"

    @api.model
    def default_get(self, fields_list):
        res = super(RentalItemHireoffWizard, self).default_get(fields_list)

        picking_type_id = self.env["stock.picking.type"].search([('name', '=', 'Rental Physical Inventory')], limit=1)
        if self._context.get('default_rental_orderline_id', False):
            rental_orderline_id = self.env["gdi.rental.order.line"].browse(self._context.get("default_rental_orderline_id"))
            if not rental_orderline_id:
                raise ValidationError(_("The rental order line you are trying to open no longer exists."))
            
            res.update({
                'rental_orderline_id': rental_orderline_id.id,
                'picking_type_id': picking_type_id.id,
                'dest_location_id': picking_type_id.default_location_src_id.id
            })

        return res
    
    picking_type_id = fields.Many2one("stock.picking.type", string="Picking Type", required=True)
    dest_location_id = fields.Many2one("stock.location", string="Dest. Location", required=True, domain=[('usage', 'in', ['internal', 'inventory'])])
    rental_orderline_id = fields.Many2one("gdi.rental.order.line", string="Rental Item")
    reason = fields.Text(string="Reason", required=True)

    def action_confirm(self):
        picking_type_id = self.env["stock.picking.type"].search([("name", "=", "Rental Physical Inventory")])
        if not picking_type_id:
            raise ValidationError(_("Operation type to perform rental hire-off not found. Please contact your system administrator !"))
        
        physical_inventory = self._create_hireoff_pi(picking_type_id)
        
        self.rental_orderline_id.write({
            'rental_state': 'hireoff' 
        })

        return self._action_view_pi(physical_inventory) 
    
    def _create_hireoff_pi(self, picking_type_id):
        """
        Create physical inventory transfer for hire-off items.
        
        Args:
            picking_type_id: stock.picking.type record for the hire-off operation
            
        Returns:
            stock.picking: Created picking record
            
        Raises:
            UserError: when picking creation or validation fails.
        """
        if not self or not self.rental_orderline_id:
            return self.env["stock.picking"]
        
        try:
            # Build all move lines first
            move_lines = self._create_hireoff_stock_moves(picking_type_id)
            
            # Create picking with all moves at once
            picking_vals = self._prepare_hireoff_picking_vals(picking_type_id, move_lines)
            picking = self.env["stock.picking"].create(picking_vals)
            
            # Validate the picking
            picking.button_validate()
            
            return picking
        except Exception as e:
            _logger.error(f"Failed to create hire-off physical inventory: {str(e)}")
            raise UserError(
                _("Failed to create hire-off physical inventory. Error: %s" % str(e))
            )


    def _prepare_hireoff_picking_vals(self, picking_type_id, move_lines):
        """
        Prepare values for creating hire-off stock picking record.
        
        Args:
            picking_type_id: stock.picking.type record
            move_lines: list of move tuples to include
            
        Returns:
            dict: Values for stock.picking creation
        """
        current_datetime = fields.Datetime.now()
        
        picking_vals = {
            'partner_id': self.rental_orderline_id.order_id.id,
            'contact_person_id': self.rental_orderline_id.order_id.id,
            'picking_type_id': picking_type_id.id,
            'location_id': picking_type_id.default_location_dest_id.id,
            'location_dest_id': picking_type_id.default_location_src_id.id,
            'move_type': 'direct',
            'scheduled_date': current_datetime,
            'date_deadline': current_datetime,
            'origin': f"{self.rental_orderline_id.name} Hire-off (IN) RO : {self.rental_orderline_id.order_id.name}",
            'src_user_id': self.env.user.id,
            'move_ids_without_package': move_lines,
            'rental_id': False,
        }
        
        return picking_vals


    def _create_hireoff_stock_moves(self, picking_type_id):
        """
        Create stock move data for hire-off items.
        Handles both regular items and set items with components.
        
        Args:
            picking_type_id: stock.picking.type record
            
        Returns:
            list: List of tuples for creating stock moves
        """
        current_datetime = fields.Datetime.now()
        move_lines = []
        sq_no = 0
        
        for line in self.rental_orderline_id:
            sq_no += 1
            
            prev_picking = self._get_hireoff_previous_picking(line)
            if not prev_picking:
                _logger.warning(f"No previous picking found for hire-off line: {line.name}")
                continue
            
            if line.item_type == 'set' and line.component_line_ids:
                # Handle set items with components
                component_moves = self._prepare_hireoff_set_component_moves(
                    line, picking_type_id, prev_picking,
                    sq_no, current_datetime
                )
                move_lines.extend(component_moves)
            else:
                # Handle regular unit items
                move_data = self._prepare_hireoff_stock_move(
                    line, picking_type_id, prev_picking,
                    sq_no, current_datetime
                )
                if move_data:
                    move_lines.append(move_data)
        
        return move_lines


    def _get_hireoff_previous_picking(self, line):
        """
        Get the previous picking/move for a hire-off line.
        
        Args:
            line: rental order line record
            
        Returns:
            stock.move: Previous stock move or False
        """
        if not line.stock_move_ids:
            return False
        
        # Get the last move (most recent)
        moves = line.stock_move_ids
        return moves[-1] if moves else False


    def _prepare_hireoff_set_component_moves(self, line, picking_type_id,
                                            prev_picking, sequence, current_datetime):
        """
        Prepare hire-off move data for all components in a set.
        
        Args:
            line: rental order line record (set item)
            picking_type_id: stock.picking.type record
            prev_picking: previous stock.move record
            sequence: base sequence number for the set
            current_datetime: current datetime
            
        Returns:
            list: List of tuples for creating component moves
        """
        component_moves = []
        component_seq = 0
        
        for component in line.component_line_ids:
            component_seq += 1
            
            prev_component_move = self._find_hireoff_component_previous_move(
                component, prev_picking
            )
            
            if not prev_component_move:
                _logger.warning(
                    f"No previous move found for component {component.product_id.name} "
                    f"in set {line.name}"
                )
                continue
            
            move_data = self._prepare_hireoff_component_move(
                line, component, picking_type_id,
                prev_component_move, sequence, component_seq, current_datetime
            )
            
            if move_data:
                component_moves.append(move_data)
        
        return component_moves


    def _find_hireoff_component_previous_move(self, component, prev_picking):
        """
        Find the previous stock move for a specific component.
        
        Args:
            component: component line record
            prev_picking: previous stock.move or stock.picking record
            
        Returns:
            stock.move: Previous move for this component or False
        """
        # If prev_picking is a stock.move, get its picking
        if prev_picking._name == 'stock.move':
            picking = prev_picking.picking_id
        else:
            picking = prev_picking
        
        if not picking:
            return False
        
        # Search for move with matching product
        component_move = picking.move_lines.filtered(
            lambda m: m.product_id.id == component.product_id.id
        )
        
        return component_move[0] if component_move else False


    def _prepare_hireoff_component_move(self, set_line, component,
                                        picking_type_id, prev_component_move,
                                        set_sequence, component_sequence, current_datetime):
        """
        Prepare hire-off stock move data for a single set component.
        
        Args:
            set_line: rental order line record (parent set)
            component: component line record
            picking_type_id: stock.picking.type record
            prev_component_move: previous stock.move for this component
            set_sequence: sequence number of the parent set
            component_sequence: sequence number within components
            current_datetime: current datetime
            
        Returns:
            tuple: Move data tuple (0, 0, dict) for creation
        """
        # Prepare move lines with lot tracking for the component
        move_line_vals = self._prepare_hireoff_component_move_lines(
            component, prev_component_move, picking_type_id, current_datetime
        )
        
        # Use component details for the move
        product = component.product_id
        name = product.product_name or product.name or component.name
        
        # Prepare main move values
        move_vals = {
            'sequence_number': set_sequence + (component_sequence * 0.01),  # e.g., 1.01, 1.02
            'name': f"{set_line.name} - {name}",
            'description_picking': name,
            'product_id': product.id,
            'product_uom': component.product_uom.id,
            'product_uom_qty': component.product_uom_qty,
            'date': current_datetime,
            'location_id': picking_type_id.default_location_dest_id.id,
            'location_dest_id': prev_component_move.location_id.id,
            'move_line_ids': move_line_vals,
        }
        
        return (0, 0, move_vals)


    def _prepare_hireoff_component_move_lines(self, component, prev_component_move,
                                            picking_type_id, current_datetime):
        """
        Prepare detailed move lines for component hire-off with lot/serial tracking.
        
        Args:
            component: component line record
            prev_component_move: previous stock.move record for this component
            picking_type_id: stock.picking.type record
            current_datetime: current datetime
            
        Returns:
            list: List of tuples for creating stock.move.line records
        """
        move_lines = []
        
        if not prev_component_move.move_line_ids:
            return move_lines
        
        for moveline in prev_component_move.move_line_ids:
            move_line_vals = {
                'product_id': component.product_id.id,
                'product_uom_id': component.product_uom.id,
                'product_uom_qty': moveline.qty_done,
                'qty_done': moveline.qty_done,
                'date': current_datetime,
                'location_id': picking_type_id.default_location_dest_id.id,
                'location_dest_id': prev_component_move.location_id.id,
                'lot_id': moveline.lot_id.id if moveline.lot_id else False,
            }
            move_lines.append((0, 0, move_line_vals))
        
        return move_lines


    def _prepare_hireoff_stock_move(self, line, picking_type_id,
                                    prev_picking, sequence, current_datetime):
        """
        Prepare hire-off stock move data with detailed move lines.
        (For non-set items)
        
        Args:
            line: rental order line record
            picking_type_id: stock.picking.type record
            prev_picking: previous stock.move record
            sequence: sequence number for the move
            current_datetime: current datetime
            
        Returns:
            tuple: Move data tuple (0, 0, dict) for creation
        """
        # Prepare move lines with lot tracking
        move_line_vals = self._prepare_hireoff_move_lines(
            line, prev_picking, picking_type_id, current_datetime
        )
        
        # Prepare main move values
        move_vals = {
            'sequence_number': sequence,
            'name': line.name,
            'description_picking': line.name,
            'product_id': line.product_id.id,
            'product_uom': line.product_uom.id,
            'product_uom_qty': line.product_uom_qty,
            'date': current_datetime,
            'location_id': picking_type_id.default_location_dest_id.id,
            'location_dest_id': prev_picking.location_id.id,
            'move_line_ids': move_line_vals,
        }
        
        return (0, 0, move_vals)


    def _prepare_hireoff_move_lines(self, line, prev_picking, picking_type_id, current_datetime):
        """
        Prepare detailed move lines with lot/serial tracking from previous picking.
        (For non-set items)
        
        Args:
            line: rental order line record
            prev_picking: previous stock.move record
            picking_type_id: stock.picking.type record
            current_datetime: current datetime
            
        Returns:
            list: List of tuples for creating stock.move.line records
        """
        move_lines = []
        
        if not prev_picking.move_line_ids:
            return move_lines
        
        for moveline in prev_picking.move_line_ids:
            move_line_vals = {
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom.id,
                'product_uom_qty': moveline.qty_done,
                'qty_done': moveline.qty_done,
                'date': current_datetime,
                'location_id': picking_type_id.default_location_dest_id.id,
                'location_dest_id': prev_picking.location_id.id,
                'lot_id': moveline.lot_id.id if moveline.lot_id else False,
            }
            move_lines.append((0, 0, move_line_vals))
        
        return move_lines
                    
        
    def _action_view_pi(self, pickings):
        self.ensure_one()
        result = self.env["ir.actions.actions"]._for_xml_id('stock.action_picking_tree_all')

        # override the context to get rid of the default filtering on operation type
        result['context'] = {'default_partner_id': self.rental_orderline_id.order_id.partner_id.id, 'default_picking_type_id': pickings.picking_type_id.id}
        
        # choose the view_mode accordingly
        if not pickings or len(pickings) > 1:
            result['domain'] = [('id', 'in', pickings.ids)]
        elif len(pickings) == 1:
            res = self.env.ref('stock.view_picking_form', False)
            form_view = [(res and res.id or False, 'form')]
            result['views'] = form_view + [(state, view) for state, view in result.get('views', []) if view != 'form']
            result['res_id'] = pickings.id
        return result        