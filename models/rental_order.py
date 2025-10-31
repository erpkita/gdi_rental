# -*- coding: utf-8 -*-
import json
import logging
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)

class GdiRentalOrder(models.Model):
    _name = "gdi.rental.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "GDI Rental Order"
    _order = 'date_order, id desc'

    @api.depends('order_line.price_total')
    def _amount_all(self):
        """
        Compute the total amounts of the RQ.
        """
        for order in self:
            amount_untaxed = amount_tax = 0.0
            for line in order.order_line:
                amount_untaxed += line.price_subtotal
                amount_tax += line.price_tax
            order.update({
                'amount_untaxed': amount_untaxed,
                'amount_tax': amount_tax,
                'amount_total': amount_untaxed + amount_tax,
            })

    name = fields.Char(string="RO Reference", 
                       require=True, copy=False, readonly=True, index=True, 
                       default=lambda self: _('New'))
    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)
    date_order = fields.Datetime(string='Order Date', 
                                 required=True, readonly=True, index=True, 
                                 states={'confirm': [('readonly', False)]}, 
                                 copy=False, 
                                 default=fields.Datetime.now, 
                                 help="Creation date of rental order")
    is_expired = fields.Boolean(compute='_compute_is_expired', string="Is expired")
    create_date = fields.Datetime(string='Creation Date', 
                                  readonly=True, index=True, 
                                  help="Date on which rental order is created.")
    user_id = fields.Many2one(
        'res.users', string='Salesperson', index=True, tracking=2, default=lambda self: self.env.user,
        domain=lambda self: "[('groups_id', '=', {}), ('share', '=', False), ('company_ids', '=', company_id)]".format(
            self.env.ref("sales_team.group_sale_salesman").id
        ),)
    fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string='Fiscal Position',
        domain="[('company_id', '=', company_id)]", check_company=True,
        help="Fiscal positions are used to adapt taxes and accounts for particular customers or sales orders/invoices."
        "The default value comes from the customer.")
    tax_country_id = fields.Many2one(
        comodel_name='res.country',
        compute='_compute_tax_country_id',
        # Avoid access error on fiscal position when reading a sale order with company != user.company_ids
        compute_sudo=True,
        help="Technical field to filter the available taxes depending on the fiscal country and fiscal position.")
    company_id = fields.Many2one('res.company', 'Company', required=True, index=True, default=lambda self: self.env.company)

    partner_id = fields.Many2one(
        'res.partner', string='Customer', readonly=True,
        states={'confirm': [('readonly', False)]},
        required=True, change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]",)
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address',
        readonly=True, required=True,
        states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",)
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address', readonly=True, required=True,
        states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",)
    
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', check_company=True,  # Unrequired company
        required=True, readonly=True, states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", tracking=1,
        help="If you change the pricelist, only newly added lines will be affected.")
    currency_id = fields.Many2one(related='pricelist_id.currency_id', depends=["pricelist_id"], store=True, ondelete="restrict")

    order_line = fields.One2many('gdi.rental.order.line', 'order_id', 
                                 string="Order Lines",
                                  states={'cancel': [('readonly', True)], 'hireoff': [('readonly', True)]}, 
                                  copy=True, auto_join=True)

    amount_untaxed = fields.Monetary(string='Untaxed Amount', store=True, compute='_amount_all', tracking=5)
    tax_totals_json = fields.Char(compute='_compute_tax_totals_json')
    amount_tax = fields.Monetary(string='Taxes', store=True, compute='_amount_all')
    amount_total = fields.Monetary(string='Total', store=True, compute='_amount_all', tracking=4)
    currency_rate = fields.Float("Currency Rate", 
                                 compute='_compute_currency_rate', store=True, 
                                 digits=(12, 6), 
                                 help='The rate of the currency to the currency of rate 1 applicable at the date of the order')
    note = fields.Html('Terms and conditions')
    state = fields.Selection([
        ('confirm', 'Confirmed'),
        ('ongoing', 'Ongoing'),
        ('hireoff', 'Hired-off'),
        ('cancel', 'Cancelled')
    ], default='confirm')

    quotation_id = fields.Many2one("rental.quotation", string="Quotation", readonly=True)

    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level')
    ], string="Date Definition Level", default='order', required=True,
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level.")

    start_date = fields.Date(string="Start Date", default=fields.Date.today, required=True)
    end_date = fields.Date(string="Initial End Date", compute="_compute_end_date", store=True)
    hireoff_date = fields.Date(string="Hire-off Date", readonly=True)

    duration = fields.Integer(string="Duration", default=1, required=True, compute="_compute_duration_from_lines", inverse="_inverse_duration", store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", required=True,
    compute="_compute_duration_from_lines",
    inverse="_inverse_duration",
    store=True
    )

    duration_string = fields.Char(string="Duration Str", compute="_compute_duration_str")

    effective_end_date = fields.Date(string="Effective End Date")
    contract_id = fields.Many2one("rental.contract", string="Active Contract")
    rental_contract_ids = fields.One2many("rental.contract", "order_id", string="Contracts Documents")
    rental_picking_ids = fields.One2many("stock.picking", "gdi_rental_id", string="RDO Documents")

    warehouse_id = fields.Many2one(
        'stock.warehouse',
        string='Warehouse',
        required=True,
        check_company=True,
        default=lambda self: self.env['stock.warehouse'].search([
            ('company_id', '=', self.env.company.id)
        ], limit=1)
    )

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        for record in self:
            record.duration_string = f"{record.duration} {dict(self._fields['duration_unit'].selection).get(record.duration_unit, 'Not Defined')}"

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
    #     if self.order_line:
    #         for line in self.order_line:
    #             line.duration = self.duration
    #             line.duration_unit = self.duration_unit

    @api.depends("order_line", "order_line.duration", "order_line.duration_unit")
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
        
        for line in self.order_line:
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
            seq_date = None
            if 'date_order' in vals:
                seq_date = fields.Datetime.context_timestamp(self, fields.Datetime.to_datetime(vals['date_order']))
            vals['name'] = "RO" + self.env['ir.sequence'].next_by_code('gdi.rental.order', sequence_date=seq_date) or _('New')
        result = super(GdiRentalOrder, self).create(vals)
        return result

    @api.onchange('partner_id')
    def onchange_partner_id(self):
        """
        Update the following fields when the partner is changed:
        - Pricelist
        - Payment terms
        - Invoice address
        - Delivery address
        - Sales Team
        """
        if not self.partner_id:
            self.update({
                'partner_invoice_id': False,
                'partner_shipping_id': False,
                'fiscal_position_id': False,
            })
            return

        self = self.with_company(self.company_id)

        addr = self.partner_id.address_get(['delivery', 'invoice'])
        partner_user = self.partner_id.user_id or self.partner_id.commercial_partner_id.user_id
        values = {
            'pricelist_id': self.partner_id.property_product_pricelist and self.partner_id.property_product_pricelist.id or False,
            'partner_invoice_id': addr['invoice'],
            'partner_shipping_id': addr['delivery'],
        }
        user_id = partner_user.id
        if not self.env.context.get('not_self_saleperson'):
            user_id = user_id or self.env.context.get('default_user_id', self.env.uid)
        if user_id and self.user_id.id != user_id:
            values['user_id'] = user_id

        self.update(values)

    @api.depends('order_line.tax_id', 'order_line.price_unit', 'amount_total', 'amount_untaxed')
    def _compute_tax_totals_json(self):
        def compute_taxes(order_line):
            price = order_line.price_unit * (1 - (order_line.discount or 0.0) / 100.0)
            order = order_line.order_id
            return order_line.tax_id._origin.compute_all(price, order.currency_id, order_line.product_uom_qty, product=order_line.product_id, partner=order.partner_shipping_id)

        account_move = self.env['account.move']
        for order in self:
            tax_lines_data = account_move._prepare_tax_lines_data_for_totals_from_object(order.order_line, compute_taxes)
            tax_totals = account_move._get_tax_totals(order.partner_id, tax_lines_data, order.amount_total, order.amount_untaxed, order.currency_id)
            order.tax_totals_json = json.dumps(tax_totals)

    @api.depends('pricelist_id', 'date_order', 'company_id')
    def _compute_currency_rate(self):
        for order in self:
            if not order.company_id:
                order.currency_rate = order.currency_id.with_context(date=order.date_order).rate or 1.0
                continue
            elif order.company_id.currency_id and order.currency_id:  # the following crashes if any one is undefined
                order.currency_rate = self.env['res.currency']._get_conversion_rate(order.company_id.currency_id, order.currency_id, order.company_id, order.date_order)
            else:
                order.currency_rate = 1.0
    
    def _order_check_rental_period(self):
        for rec in self:
            if not rec.start_date:
                raise ValidationError(_(f"Rental period start date is not defined. Please define it before starting the rental."))
            if not rec.end_date:
                raise ValidationError(_(f"Rental period end date is not defined. Please define it before starting the rental."))            

    def action_view_rental_contract(self, contract_id):
        action = self.env['ir.actions.actions']._for_xml_id("gdi_rental.action_gdi_rental_contracts_view")
        form_view = [(self.env.ref('gdi_rental.view_gdi_rental_contract_tree_form').id, 'form')]
        if 'views' in action:
            action['views'] = form_view + [(state,view) for state,view in action['views'] if view != 'form']
        else:
            action['views'] = form_view
        action['res_id'] = contract_id.id

        return action

    def action_generate_contract(self):
        for rec in self:
            # if rec.date_definition_level == "order":
            #     rec._order_check_rental_period()
            # else:
            #     for line in rec.order_line:
            #         line.check_rental_period()
            
            contract_id = self.env["rental.contract"].create(rec._prepare_contract_vals())
            for line in rec.order_line:
                contract_line_values = self._prepare_contract_line(line)
                contract_line_values.update({'contract_id': contract_id.id})
                self.env["rental.contract.line"].create(contract_line_values)
            
            rec.write({'state': 'ongoing'}) 
            # return rec.action_view_rental_contract(contract_id)
            return contract_id
    
    def _prepare_contract_vals(self):
        partner = self.partner_id
        contract_vals = {
            'partner_id': partner.id or False,
            'pricelist_id': self.pricelist_id.id or False,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': self.user_id.id or False,
            'order_id': self.id or False,
            'company_id': self.company_id.id or False,
            # 'date_definition_level': self.date_definition_level or False,
            # 'start_date': self.start_date or False,
            # 'end_date': self.end_date or False,
            'currency_id': self.currency_id.id or False,
            'contract_line_ids' : [],
            'fiscal_position_id': self.fiscal_position_id.id
        }

        return contract_vals

    def _prepare_contract_line(self, line):
        contract_line_vals = {
            'ro_line_id': line.id,
            'name': line.name,
            'item_type': line.item_type,
            'item_code': line.item_code,
            'product_id': line.product_id.id or False,
            'product_uom': line.product_uom.id or False,
            'product_uom_qty': line.product_uom_qty,
            'product_uom_txt': line.product_uom_txt or "",
            'price_unit': line.price_unit,
            'tax_id' : line.tax_id.ids or False,
            'duration': line.duration,
            # 'date_definition_level': line.date_definition_level or False,
            # 'start_date': line.start_date or False,
            # 'end_date': line.end_date or False,
            'duration_unit': line.duration_unit
        }
        if line.item_type == 'set':
            component_records = []
            for rec in line.component_line_ids:
                component_records.append((0, 0, {
                    'product_id': rec.product_id.id or False,
                    'name': rec.name or False,
                    'price_unit': rec.price_unit or 0.0,
                    'product_uom_qty': rec.product_uom_qty or 0.0,
                    'product_uom': rec.product_uom.id
                }))

            contract_line_vals.update({'component_line_ids': component_records})
        return contract_line_vals
                
    def action_create_contract(self): 
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rental.contract.creation.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref('gdi_rental.gdi_rental_contract_creation_wizard_form_view').id,
            'target': 'new',
            'context': {
                'default_rental_id': self.id,
            }
        }

    def action_cancel(self):
        pass

    def action_print_order(self):
        pass

    def _prepare_rental_contract_vals(self, order):
        if not order:
            raise ValidationError(_("Could not process rental order. Please contact administrator !"))
        return {
            'order_id': order.id or False,
            'partner_id': order.partner_id.id or False,
            'customer_reference': order.customer_reference or '',
            'customer_po_number': order.customer_po_number or '',
            'duration': order.duration or 1,
            'duration_unit': order.duration_unit or 'month',
            'start_date': order.start_date or False,
            'end_date': order.end_date or False,
            'pricelist_id': order.pricelist_id.id or False,
            'fiscal_position_id': order.fiscal_position_id.id or False,
        }
    
    def action_start_rental(self):
        for rec in self:
            contract_vals = rec._prepare_rental_contract_vals(rec)
            contract_line_ids = []
            for line in rec.order_line:
                line.check_rental_period()
                contract_line_ids.append((
                    0, 
                    0, 
                    line._get_contract_line_vals()
                ))
            contract_vals.update({'contract_line_ids': contract_line_ids})
            contract_id = self.env['rental.contract'].create(contract_vals)

            if not contract_id:
                raise ValidationError(_("Error while creating contract. Please contact administrator !"))
            
            # auto sign the contract because its first rental and we expect to auto generate DO.
            contract_id.with_context({'new_rdo': True}).create_do()

            rec.write({
                'state': 'ongoing', 
                'effective_end_date': rec.end_date,
                'contract_id': contract_id.id
            })

    def action_hireoff(self):
        """
        Process hire-off for the entire rental order.
        Creates physical inventory to return all active rental items.
        """
        for rec in self:
            # Validate there are active lines to hire-off
            active_lines = rec.order_line.filtered(lambda x: x.rental_state == 'active')
            if not active_lines:
                raise ValidationError(_("No active rental items found to hire-off."))
            
            # Find the rental physical inventory picking type
            picking_type_id = self.env["stock.picking.type"].search(
                [('name', '=', 'Rental Physical Inventory')], 
                limit=1
            )
            if not picking_type_id:
                raise ValidationError(
                    _("Operation type 'Rental Physical Inventory' not found. "
                    "Please contact your system administrator!")
                )
            
            try:
                # Create physical inventory for hire-off
                physical_inventory_hireoff = rec._create_physical_inventory_hireoff(picking_type_id)
                
                # Update rental order state
                rec.write({
                    'state': 'hireoff',
                    'hireoff_date': fields.Datetime.now(),
                })
                
                # Update all active lines to hireoff state
                active_lines.write({
                    'rental_state': 'hireoff'
                })
                
                # Log the hire-off action
                rec.message_post(
                    body=_("Rental order hired-off. Physical inventory created: %s") % 
                        physical_inventory_hireoff.name
                )
                
            except Exception as e:
                _logger.error(f"Failed to process hire-off for order {rec.name}: {str(e)}")
                raise ValidationError(
                    _("Failed to process hire-off. Error: %s") % str(e)
                )


    def _create_physical_inventory_hireoff(self, picking_type_id):
        """
        Create physical inventory transfer for hire-off of active rental items.
        
        Args:
            picking_type_id: stock.picking.type record for the hire-off operation
            
        Returns:
            stock.picking: Created picking record
            
        Raises:
            UserError: when picking creation or validation fails.
        """
        if not self or not self.order_line:
            return self.env["stock.picking"]
        
        try:
            # Build all move lines for active rental items
            move_lines = self._create_hireoff_stock_moves(picking_type_id)
            
            if not move_lines:
                raise UserError(_("No stock moves could be created for hire-off."))
            
            # Create picking with all moves at once
            picking_vals = self._prepare_hireoff_picking_vals(picking_type_id, move_lines)
            picking = self.env["stock.picking"].create(picking_vals)
            
            # Validate the picking
            picking.button_validate()
            
            return picking
            
        except Exception as e:
            _logger.error(f"Failed to create physical inventory for hire-off: {str(e)}")
            raise UserError(
                _("Failed to create physical inventory for hire-off. Error: %s") % str(e)
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
            'partner_id': self.partner_id.id,
            'contact_person_id': self.partner_id.id,
            'picking_type_id': picking_type_id.id,
            'location_id': picking_type_id.default_location_dest_id.id,
            'location_dest_id': picking_type_id.default_location_src_id.id,
            'move_type': "direct",
            'scheduled_date': current_datetime,
            'date_deadline': current_datetime,
            'origin': f"{self.name} - Hire-off (IN)",
            'customer_po': self.customer_po_number,
            'src_user_id': self.env.user.id,
            'move_ids_without_package': move_lines,
            'rental_id': self.id,
        }
        
        return picking_vals


    def _create_hireoff_stock_moves(self, picking_type_id):
        """
        Create stock move data for hire-off items.
        Only processes lines with rental_state = 'active'.
        Handles both regular items and set items with components.
        
        Args:
            picking_type_id: stock.picking.type record
            
        Returns:
            list: List of tuples for creating stock moves
        """
        current_datetime = fields.Datetime.now()
        move_lines = []
        sq_no = 0
        
        # Filter only active rental lines
        active_lines = self.order_line.filtered(lambda x: x.rental_state == 'active')
        
        for line in active_lines:
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
        
        # Get the last move (most recent outgoing move)
        moves = line.stock_move_ids.sorted(key=lambda m: m.date, reverse=True)
        return moves[0] if moves else False


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
            'sequence_number': set_sequence + (component_sequence * 0.01),
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


    def open_related_contract(self):
        for rec in self:
            return {
                "type": "ir.actions.act_window",
                "res_id": rec.contract_id.id or False,
                "res_model": "rental.contract",
                "view_mode": "form"
            }