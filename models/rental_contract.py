# -*- coding: utf-8 -*-

import logging

from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta
import datetime

_logger = logging.getLogger(__name__)


class RentalContract(models.Model):
    _name = "rental.contract"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Rental Contract"

    order_id = fields.Many2one('gdi.rental.order', string='RO Reference', required=True,
                                   ondelete='cascade', index=True, copy=False)
    name = fields.Text(string='Name', required=True, default=lambda self: _('New'))
    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)
    start_date = fields.Date(string="Start Date", required=False)
    end_date = fields.Date(string="End Date", compute="_compute_end_date", store=True)

    duration = fields.Integer(string="Duration", default=1, required=True, compute="_compute_duration_from_lines", inverse="_inverse_duration", store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", required=True,
    compute="_compute_duration_from_lines",
    inverse="_inverse_duration",
    store=True,
    default='month'
    )    

    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level')
    ], string="Date Definition Lvl", default='order',
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level.")
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', check_company=True,  # Unrequired company
        required=True, domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", tracking=1,
        help="If you change the pricelist, only newly added lines will be affected.")
    currency_id = fields.Many2one(related='pricelist_id.currency_id', depends=["pricelist_id"], store=True, ondelete="restrict")

    company_id = fields.Many2one('res.company', 'Company', required=True, index=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one(
        'res.partner', string='Customer',
        required=True, change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]",)
    user_id = fields.Many2one(
        'res.users', string='Salesperson', index=True, tracking=2, default=lambda self: self.env.user,
        domain=lambda self: "[('groups_id', '=', {}), ('share', '=', False), ('company_ids', '=', company_id)]".format(
            self.env.ref("sales_team.group_sale_salesman").id
        ),)
    contract_line_ids = fields.One2many("rental.contract.line", "contract_id", string="Rental Items")

    fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string='Fiscal Position',
        domain="[('company_id', '=', company_id)]", check_company=True,
        help="Fiscal positions are used to adapt taxes and accounts for particular customers or sales orders/invoices."
        "The default value comes from the customer.")
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('signed', 'Signed'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], string="Status", default='draft')

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        required=True,
        check_company=True,
        default=lambda self: self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1)
    )
    
    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        for record in self:
            if not record.start_date:
                record.end_date = False
                continue
                
            if record.duration_unit == 'hour':
                # For hours, we need to handle it differently as Date fields don't have hours
                # This is a simplified approach - you might need to convert to datetime if precision is critical
                record.end_date = record.start_date + relativedelta(hours=record.duration)
            elif record.duration_unit == 'day':
                record.end_date = record.start_date + relativedelta(days=record.duration)
            elif record.duration_unit == 'week':
                record.end_date = record.start_date + relativedelta(weeks=record.duration)
            elif record.duration_unit == 'month':
                record.end_date = record.start_date + relativedelta(months=record.duration)

    @api.model
    def _convert_to_days(self, duration, duration_unit):
        """Convert any duration unit to approximate days for comparison"""
        if duration_unit == 'hour':
            return duration / 24
        elif duration_unit == 'day':
            return duration
        elif duration_unit == 'week':
            return duration * 7
        elif duration_unit == 'month':
            return duration * 30  # Approximation
        return 0
    
    # @api.onchange('duration', 'duration_unit')
    # def _onchange_header_duration(self):
    #     """Update all line durations when header duration changes"""
    #     if self.contract_line_ids:
    #         for line in self.contract_line_ids:
    #             line.duration = self.duration
    #             line.duration_unit = self.duration_unit

    @api.depends("contract_line_ids", "contract_line_ids.duration", "contract_line_ids.duration_unit")
    def _compute_duration_from_lines(self):
        for record in self:
            record.update_header_duration()

    def _inverse_duration(self):
        # Just allow the fields to be editable.
        pass    
    
    def update_header_duration(self):
        """Update header duration based on longest line item"""
        longest_days = 0
        longest_duration = self.duration
        longest_unit = self.duration_unit
        
        for line in self.contract_line_ids:
            line_days = self._convert_to_days(line.duration, line.duration_unit)
            if line_days > longest_days:
                longest_days = line_days
                longest_duration = line.duration
                longest_unit = line.duration_unit
        
        self.duration = longest_duration
        self.duration_unit = longest_unit

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = "CONTRACT-" + self.env['ir.sequence'].next_by_code('rental.contract.seq') or _('New')        
        result = super(RentalContract, self).create(vals)
        return result
    
    @api.onchange('partner_id')
    def onchange_partner_id(self):
        if not self.partner_id:
            return

        self = self.with_company(self.company_id)

        addr = self.partner_id.address_get(['delivery', 'invoice'])
        partner_user = self.partner_id.user_id or self.partner_id.commercial_partner_id.user_id
        values = {
            'pricelist_id': self.partner_id.property_product_pricelist and self.partner_id.property_product_pricelist.id or False,
        }
        user_id = partner_user.id
        if not self.env.context.get('not_self_saleperson'):
            user_id = user_id or self.env.context.get('default_user_id', self.env.uid)
        if user_id and self.user_id.id != user_id:
            values['user_id'] = user_id

        self.update(values)


    def create_do(self):
        """
        Create delivery orders for rental contracts.
        
        Returns:
            recordset: Created stock.picking records
        
        Raises:
            UserError: When required picking type is not found
            ValidationError: When delivery order creation fails
        """
        if not self:
            return self.env['stock.picking']
        
        picking_type_id = self.env["stock.picking.type"].search([(
            'name', '=', 'Rental Physical Inventory'
        )])
        if not picking_type_id:
            raise ValidationError("Operation type not found. Please contact your system administrator !")

        created_pickings = self.env['stock.picking']
        
        for contract in self:
            # try:
            if not self._context.get('new_rdo'):
                self._create_physical_inventory(picking_type_id)

            picking = self._create_single_delivery_order(contract)
            if picking:
                created_pickings |= picking
                
                picking.action_confirm()
                # Update contract state only after successful creation
                contract.write({'state': 'signed'})
            # except Exception as e:
            #     _logger.error(f"Failed to create delivery order for contract {contract.name}: {str(e)}")
            #     raise ValidationError(
            #         _("Failed to create delivery order for contract %s. Error: %s") % (contract.name, str(e))
            #     )
        
        return created_pickings

    def _create_single_delivery_order(self, contract):
        """
        Create a single delivery order for a rental contract.
        
        Args:
            contract: rental contract record
            
        Returns:
            stock.picking: Created picking record
        """
        picking_type = self._get_picking_type()
        if not picking_type:
            raise UserError(_("No suitable picking type found for rental delivery orders"))
        
        # Create the main picking record
        picking_vals = self._prepare_picking_vals(contract, picking_type)
        picking = self.env["stock.picking"].create(picking_vals)
        
        # Create stock moves for all items
        self._create_stock_moves(contract, picking, picking_type)
        
        return picking

    def _get_picking_type(self):
        """Get the appropriate picking type for rental deliveries."""
        PickingType = self.env['stock.picking.type']
        
        # Try rental-specific picking type first
        picking_type = PickingType.search([('name', '=', 'Rental Delivery Orders')], limit=1)
        
        if not picking_type:
            # Fallback to general delivery orders
            picking_type = PickingType.search([('name', '=', 'Delivery Orders')], limit=1)
        
        return picking_type

    def _prepare_picking_vals(self, contract, picking_type):
        """
        Prepare values for creating stock picking record.
        
        Args:
            contract: rental contract record
            picking_type: stock.picking.type record
            
        Returns:
            dict: Values for stock.picking creation
        """
        current_datetime = fields.Datetime.now()
        
        picking_vals = {
            'partner_id': contract.partner_id.id,
            'contact_person_id': contract.partner_id.id,
            'is_rental_do': True,
            'gdi_rental_id': contract.order_id.id,
            'rental_contract_id': contract.id,
            'picking_type_id': picking_type.id,
            'origin': contract.name,
            'customer_po': contract.customer_po_number,
            'move_type': 'direct',
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': contract.partner_id.property_stock_customer.id,
            'src_user_id': contract.user_id.id,
            'scheduled_date': current_datetime,
            'date_deadline': current_datetime,
            # Don't set date_done for draft pickings - it should be set when actually done
        }
        
        # Add rental items
        rental_items = self._prepare_rental_items(contract)
        picking_vals['rental_order_item_ids'] = rental_items
        
        return picking_vals

    def _prepare_rental_items(self, contract):
        """
        Prepare rental order items from contract lines.
        Use the same simple approach that worked in the old code.
        
        Args:
            contract: rental contract record
            
        Returns:
            list: List of tuples for creating rental order items
        """
        rental_items = []
        
        for line in contract.contract_line_ids:
            item_vals = {
                'name': line.name or '',
                'item_code': line.item_code or '',
                'sequence': line.sequence or 0,
                'contract_line_id': line.id,
                'product_id': line.product_id.id,
                'product_template_id': line.product_template_id.id,
                'product_uom_qty': line.product_uom_qty or 1.0,
                'product_uom': line.product_uom.id,
                'product_uom_category_id': line.product_uom_category_id.id,
                'product_uom_txt': line.product_uom_txt or 'SET',
                'price_unit': line.price_unit or 0.0,
                # Use the same simple approach as the old working code
                'start_date': line.start_date or False,
                'end_date': line.end_date or False,
                'duration': line.duration,
                'duration_unit': line.duration_unit,
            }
            rental_items.append((0, 0, item_vals))
        
        return rental_items

    def _create_stock_moves(self, contract, picking, picking_type):
        """
        Create stock moves for rental items.
        
        Args:
            contract: rental contract record
            picking: stock.picking record
            picking_type: stock.picking.type record
        """
        current_datetime = fields.Datetime.now()
        
        for rental_item in picking.rental_order_item_ids:
            contract_line = rental_item.contract_line_id
            
            if contract_line.item_type != 'set':
                # Create single move for non-set items
                self._create_stock_move(
                    contract_line, contract, picking, picking_type, 
                    rental_item, current_datetime
                )
            else:
                # Create moves for set components
                self._create_set_component_moves(
                    contract_line, contract, picking, picking_type, 
                    rental_item, current_datetime
                )
            
            # Update rental order line state
            if contract_line.ro_line_id:
                contract_line.ro_line_id.write({'rental_state': 'active'})

    def _create_stock_move(self, contract_line, contract, picking, picking_type, 
                        rental_item, current_datetime, component=None):
        """
        Create a single stock move.
        
        Args:
            contract_line: contract line record
            contract: rental contract record
            picking: stock.picking record
            picking_type: stock.picking.type record
            rental_item: rental order item record
            current_datetime: current datetime
            component: component record (for set items)
        """
        if component:
            # For set components
            product = component.product_id
            name = product.product_name or product.name
            price_unit = component.price_unit or 0.0
            qty = component.product_uom_qty or 1.0
            uom = component.product_uom
        else:
            # For regular items
            product = contract_line.product_id
            name = contract_line.name or product.name
            price_unit = contract_line.price_unit or 0.0
            qty = contract_line.product_uom_qty or 1.0
            uom = contract_line.product_uom
        
        move_vals = {
            'sequence_number': contract_line.sequence or 0,
            'name': name,
            'description_picking': name,
            'product_id': product.id,
            'product_uom': uom.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': contract.partner_id.property_stock_customer.id,
            'date': current_datetime,
            'price_unit': price_unit,
            'product_uom_qty': qty,
            'picking_id': picking.id,
            'rental_order_item_id': rental_item.id,
            'ro_line_id': contract_line.ro_line_id.id,
            'rental_order_component_id': component.id if component else False
        }

        return self.env["stock.move"].create(move_vals)

    def _create_set_component_moves(self, contract_line, contract, picking, 
                                picking_type, rental_item, current_datetime):
        """
        Create stock moves for set components.
        
        Args:
            contract_line: contract line record
            contract: rental contract record
            picking: stock.picking record
            picking_type: stock.picking.type record
            rental_item: rental order item record
            current_datetime: current datetime
        """
        ro_line_component_ids = contract_line.ro_line_id.component_line_ids
        for component in ro_line_component_ids:
            self._create_stock_move(
                contract_line, contract, picking, picking_type,
                rental_item, current_datetime, component=component
            )

    def _create_physical_inventory(self, picking_type_id):
        """
        Create physical inventory transfer for rental extension items.

        Args:
            picking_type_id: stock.picking.type record for the return operation

        Returns:
            stock.picking: Created picking record

        Raises:
            UserError: when picking creation or validation fails.
        """

        if not self or not self.contract_line_ids:
            return self.env["stock.picking"]

        try:
            # Build all move lines first
            move_lines = self._create_return_stock_moves(picking_type_id)
            
            # Create picking with all moves at once
            picking_vals = self._prepare_return_picking_vals(picking_type_id, move_lines)
            picking = self.env["stock.picking"].create(picking_vals)

            # Validate the picking
            picking.button_validate()

            return picking
        except Exception as e:
            _logger.error(f"Failed to create physical inventory for rental extension: {str(e)}")
            raise UserError(
                _("Failed to create physical inventory. Error: %s" % str(e))
            )
    
    def _prepare_return_picking_vals(self, picking_type_id, move_lines):
        """
        Prepare values for creating return stock picking record.

        Args: 
            picking_type_id: stock.picking.type record
            move_lines: list of move tuples to include

        Returns:
            dict: Values for stock.picking creation
        """
        current_datetime = fields.Datetime.now()

        picking_vals = {
            'partner_id': self.partner_id.id,
            'contact_person_id': self.order_id.partner_id.id,
            'picking_type_id': picking_type_id.id,
            'location_id': picking_type_id.default_location_dest_id.id,
            'location_dest_id': picking_type_id.default_location_src_id.id,
            'move_type': 'direct',
            'scheduled_date': current_datetime,
            'date_deadline': current_datetime,
            'origin': f"{self.order_id.name} Extend (IN)",
            'customer_po': self.customer_po_number,
            'src_user_id': self.env.user.id,
            'move_ids_without_package': move_lines,  # Add moves here
            'rental_id': False,
        }

        return picking_vals
    
    def _create_return_stock_moves(self, picking_type_id):
        """
        Create stock move data for rental extension return items.
        Handles both regular items and set items with components.
        Returns list of tuples for move creation.

        Args:
            picking_type_id: stock.picking.type record
            
        Returns:
            list: List of tuples for creating stock moves
        """
        current_datetime = fields.Datetime.now()
        move_lines = []
        sq_no = 0
        
        for line in self.contract_line_ids:
            sq_no += 1 

            prev_picking = self._get_previous_picking(line)
            if not prev_picking:
                _logger.warning(f"No previous picking found for rental extend line: {line.name}")
                continue

            if line.item_type == 'set' and line.component_line_ids:
                # Handle set items with components
                component_moves = self._prepare_return_set_component_moves(
                    line, picking_type_id, prev_picking,
                    sq_no, current_datetime
                )
                move_lines.extend(component_moves)
            else:
                # Handle regular unit items
                move_data = self._prepare_return_stock_move(
                    line, picking_type_id, prev_picking, 
                    sq_no, current_datetime
                )
                if move_data:
                    move_lines.append(move_data)
        
        return move_lines
    
    def _get_previous_picking(self, line):
        """
        Get the previous picking/move for a rental line.
        
        Args:
            line: rental extension line record
            
        Returns:
            stock.move: Previous stock move or False
        """
        if not line.ro_line_id or not line.ro_line_id.stock_move_ids:
            return False
        
        moves = line.ro_line_id.stock_move_ids
        return moves[-1] if moves else False
    
    def _prepare_return_set_component_moves(self, line, picking_type_id,
                                           prev_picking, sequence, current_datetime):
        """
        Prepare return move data for all components in a set.
        
        Args:
            line: rental extension line record (set item)
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

            prev_component_move = self._find_component_previous_move(
                component, prev_picking
            )

            if not prev_component_move:
                _logger.warning(
                    f"No previous move found for component {component.product_id.name} "
                    f"in set {line.name}"
                )
                continue

            move_data = self._prepare_return_component_move(
                line, component, picking_type_id,
                prev_component_move, sequence, component_seq, current_datetime
            )
            
            if move_data:
                component_moves.append(move_data)
        
        return component_moves

    def _find_component_previous_move(self, component, prev_picking):
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

    def _prepare_return_component_move(self, set_line, component, 
                                    picking_type_id, prev_component_move,
                                    set_sequence, component_sequence, current_datetime):
        """
        Prepare return stock move data for a single set component.
        
        Args:
            set_line: rental extension line record (parent set)
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
        move_line_vals = self._prepare_component_return_move_lines(
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

    def _prepare_component_return_move_lines(self, component, prev_component_move,
                                            picking_type_id, current_datetime):
        """
        Prepare detailed move lines for component returns with lot/serial tracking.
        
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

    def _prepare_return_stock_move(self, line, picking_type_id, 
                                prev_picking, sequence, current_datetime):
        """
        Prepare return stock move data with detailed move lines.
        (For non-set items)
        
        Args:
            line: rental extension line record
            picking_type_id: stock.picking.type record
            prev_picking: previous stock.move record
            sequence: sequence number for the move
            current_datetime: current datetime
            
        Returns:
            tuple: Move data tuple (0, 0, dict) for creation
        """
        # Prepare move lines with lot tracking
        move_line_vals = self._prepare_return_move_lines(
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

    def _prepare_return_move_lines(self, line, prev_picking, picking_type_id, current_datetime):
        """
        Prepare detailed move lines with lot/serial tracking from previous picking.
        (For non-set items)
        
        Args:
            line: rental extension line record
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