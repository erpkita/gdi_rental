# -*- coding: utf-8 -*-

import json
import logging
from datetime import datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError, UserError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)


class GdiRentalOrder(models.Model):
    _name = 'gdi.rental.order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'GDI Rental Order'
    _order = 'date_order desc, id desc'

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    name = fields.Char(
        string='RO Reference', required=True, copy=False,
        readonly=True, index=True, default=lambda self: _('New'))
    state = fields.Selection([
        ('confirm', 'Confirmed'),
        ('ongoing', 'Ongoing'),
        ('hireoff', 'Hired-off'),
        ('cancel', 'Cancelled'),
    ], string='Status', default='confirm', tracking=True)

    # --- Partner ---
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, readonly=True,
        states={'confirm': [('readonly', False)]},
        change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]")
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address', required=True, readonly=True,
        states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address', required=True, readonly=True,
        states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    customer_reference = fields.Char(string='Customer Reference', copy=False)
    customer_po_number = fields.Char(string='Customer Ref. PO', copy=False)

    # --- Dates & Duration ---
    date_order = fields.Datetime(
        string='Order Date', required=True, readonly=True, index=True,
        copy=False, default=fields.Datetime.now,
        states={'confirm': [('readonly', False)]})
    create_date = fields.Datetime(
        string='Creation Date', readonly=True, index=True)
    start_date = fields.Date(
        string='Start Date', default=fields.Date.today, required=True)
    end_date = fields.Date(
        string='Initial End Date', compute='_compute_end_date', store=True)
    hireoff_date = fields.Date(string='Hire-off Date', readonly=True)
    effective_end_date = fields.Date(string='Effective End Date')

    duration = fields.Integer(
        string='Duration', default=1, required=True,
        compute='_compute_duration_from_lines',
        inverse='_inverse_duration', store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'Weeks'),
        ('month', 'Months'),
    ], string='Unit', required=True, default='month',
        compute='_compute_duration_from_lines',
        inverse='_inverse_duration', store=True)
    duration_string = fields.Char(
        string='Duration Str', compute='_compute_duration_str')

    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level'),
    ], string='Date Definition Level', default='order', required=True)

    # --- Company / Currency ---
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', required=True,
        check_company=True, readonly=True,
        states={'confirm': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",
        tracking=1)
    currency_id = fields.Many2one(
        related='pricelist_id.currency_id',
        depends=['pricelist_id'], store=True, ondelete='restrict')
    fiscal_position_id = fields.Many2one(
        'account.fiscal.position', string='Fiscal Position',
        domain="[('company_id', '=', company_id)]", check_company=True)
    tax_country_id = fields.Many2one(
        'res.country', compute='_compute_tax_country_id',
        compute_sudo=True)

    # --- Salesperson ---
    user_id = fields.Many2one(
        'res.users', string='Salesperson', index=True, tracking=2,
        default=lambda self: self.env.user,
        domain=lambda self: (
            "[('groups_id', '=', %s), ('share', '=', False), "
            "('company_ids', '=', company_id)]"
            % self.env.ref('sales_team.group_sale_salesman').id))
    is_expired = fields.Boolean(
        compute='_compute_is_expired', string='Is expired')

    # --- Lines ---
    order_line = fields.One2many(
        'gdi.rental.order.line', 'order_id', string='Order Lines',
        states={'cancel': [('readonly', True)], 'hireoff': [('readonly', True)]},
        copy=True, auto_join=True)

    # --- Totals ---
    amount_untaxed = fields.Monetary(
        string='Untaxed Amount', store=True, compute='_compute_amounts', tracking=5)
    amount_tax = fields.Monetary(
        string='Taxes', store=True, compute='_compute_amounts')
    amount_total = fields.Monetary(
        string='Total', store=True, compute='_compute_amounts', tracking=4)
    tax_totals_json = fields.Char(compute='_compute_tax_totals_json')
    currency_rate = fields.Float(
        string='Currency Rate', compute='_compute_currency_rate',
        store=True, digits=(12, 6))

    # --- Links ---
    quotation_id = fields.Many2one(
        'rental.quotation', string='Quotation', readonly=True)
    contract_id = fields.Many2one(
        'rental.contract', string='Active Contract')
    rental_contract_ids = fields.One2many(
        'rental.contract', 'order_id', string='Contract Documents')
    rental_picking_ids = fields.One2many(
        'stock.picking', 'gdi_rental_id', string='RDO Documents')

    # --- Warehouse ---
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', required=True,
        check_company=True,
        default=lambda self: self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1))

    # --- Misc ---
    note = fields.Html('Terms and conditions')

    # -------------------------------------------------------------------------
    # COMPUTE METHODS
    # -------------------------------------------------------------------------

    @api.depends('order_line.price_total')
    def _compute_amounts(self):
        for order in self:
            untaxed = tax = 0.0
            for line in order.order_line:
                untaxed += line.price_subtotal
                tax += line.price_tax
            order.amount_untaxed = untaxed
            order.amount_tax = tax
            order.amount_total = untaxed + tax

    @api.depends('start_date', 'duration', 'duration_unit')
    def _compute_end_date(self):
        delta_map = {
            'hour': lambda d: relativedelta(hours=d),
            'day': lambda d: relativedelta(days=d),
            'week': lambda d: relativedelta(weeks=d),
            'month': lambda d: relativedelta(months=d),
        }
        for rec in self:
            if not rec.start_date or not rec.duration_unit:
                rec.end_date = False
                continue
            delta_fn = delta_map.get(rec.duration_unit)
            rec.end_date = rec.start_date + delta_fn(rec.duration) if delta_fn else False

    @api.depends('order_line', 'order_line.duration', 'order_line.duration_unit')
    def _compute_duration_from_lines(self):
        for rec in self:
            if not rec.order_line:
                continue
            longest_days = 0
            best_duration = rec.duration or 1
            best_unit = rec.duration_unit or 'month'
            for line in rec.order_line:
                line_days = self._to_days(line.duration, line.duration_unit)
                if line_days > longest_days:
                    longest_days = line_days
                    best_duration = line.duration
                    best_unit = line.duration_unit
            rec.duration = best_duration
            rec.duration_unit = best_unit

    def _inverse_duration(self):
        pass  # Allow direct edits

    @api.depends('duration', 'duration_unit')
    def _compute_duration_str(self):
        labels = dict(self._fields['duration_unit'].selection)
        for rec in self:
            rec.duration_string = f"{rec.duration} {labels.get(rec.duration_unit, '')}"

    @api.depends('order_line.tax_id', 'order_line.price_unit',
                 'amount_total', 'amount_untaxed')
    def _compute_tax_totals_json(self):
        def _compute_taxes(line):
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            return line.tax_id._origin.compute_all(
                price, line.order_id.currency_id,
                line.product_uom_qty, product=line.product_id,
                partner=line.order_id.partner_shipping_id)

        AccountMove = self.env['account.move']
        for order in self:
            tax_data = AccountMove._prepare_tax_lines_data_for_totals_from_object(
                order.order_line, _compute_taxes)
            tax_totals = AccountMove._get_tax_totals(
                order.partner_id, tax_data,
                order.amount_total, order.amount_untaxed, order.currency_id)
            order.tax_totals_json = json.dumps(tax_totals)

    @api.depends('pricelist_id', 'date_order', 'company_id')
    def _compute_currency_rate(self):
        for order in self:
            if order.company_id and order.company_id.currency_id and order.currency_id:
                order.currency_rate = self.env['res.currency']._get_conversion_rate(
                    order.company_id.currency_id, order.currency_id,
                    order.company_id, order.date_order)
            else:
                order.currency_rate = 1.0

    def _compute_tax_country_id(self):
        for rec in self:
            if rec.fiscal_position_id.foreign_vat:
                rec.tax_country_id = rec.fiscal_position_id.country_id
            else:
                rec.tax_country_id = rec.company_id.account_fiscal_country_id

    def _compute_is_expired(self):
        today = fields.Date.today()
        for rec in self:
            rec.is_expired = bool(rec.end_date and rec.end_date < today)

    # -------------------------------------------------------------------------
    # HELPERS
    # -------------------------------------------------------------------------

    @staticmethod
    def _to_days(duration, unit):
        """Convert duration to approximate days for comparison."""
        multipliers = {'hour': 1 / 24, 'day': 1, 'week': 7, 'month': 30}
        return duration * multipliers.get(unit, 0)

    # -------------------------------------------------------------------------
    # CRUD
    # -------------------------------------------------------------------------

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            seq_date = None
            if 'date_order' in vals:
                seq_date = fields.Datetime.context_timestamp(
                    self, fields.Datetime.to_datetime(vals['date_order']))
            seq = self.env['ir.sequence'].next_by_code(
                'gdi.rental.order', sequence_date=seq_date) or _('New')
            vals['name'] = f"RO{seq}"
        return super().create(vals)

    # -------------------------------------------------------------------------
    # ONCHANGES
    # -------------------------------------------------------------------------

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        if not self.partner_id:
            self.partner_invoice_id = False
            self.partner_shipping_id = False
            self.fiscal_position_id = False
            return

        self = self.with_company(self.company_id)
        addr = self.partner_id.address_get(['delivery', 'invoice'])
        self.partner_invoice_id = addr['invoice']
        self.partner_shipping_id = addr['delivery']
        self.pricelist_id = (
            self.partner_id.property_product_pricelist.id
            if self.partner_id.property_product_pricelist else False)

        partner_user = (
            self.partner_id.user_id
            or self.partner_id.commercial_partner_id.user_id)
        if partner_user and not self.env.context.get('not_self_saleperson'):
            self.user_id = partner_user

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_print_order(self):
        pass

    def action_create_contract(self):
        """Open the contract creation wizard."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'rental.contract.creation.wizard',
            'view_mode': 'form',
            'view_id': self.env.ref(
                'gdi_rental.gdi_rental_contract_creation_wizard_form_view').id,
            'target': 'new',
            'context': {'default_rental_id': self.id},
        }

    def action_generate_contract(self):
        """Generate contract directly from order lines."""
        self.ensure_one()
        contract = self.env['rental.contract'].create(
            self._prepare_contract_vals())
        for line in self.order_line:
            line_vals = self._prepare_contract_line(line)
            line_vals['contract_id'] = contract.id
            self.env['rental.contract.line'].create(line_vals)
        self.write({'state': 'ongoing'})
        return contract

    def action_start_rental(self):
        """Start the rental: create contract, auto-sign, generate DO."""
        for rec in self:
            for line in rec.order_line:
                line.check_rental_period()

            contract_vals = rec._prepare_rental_contract_vals()
            contract_vals['contract_line_ids'] = [
                (0, 0, line._get_contract_line_vals())
                for line in rec.order_line
            ]
            contract = self.env['rental.contract'].create(contract_vals)
            if not contract:
                raise ValidationError(
                    _("Error creating contract. Please contact administrator!"))

            # Auto-sign and generate DO
            contract.with_context(new_rdo=True).create_do()

            rec.write({
                'state': 'ongoing',
                'effective_end_date': rec.end_date,
                'contract_id': contract.id,
            })

    def action_hireoff(self):
        """Process hire-off: return all active rental items."""
        for rec in self:
            active_lines = rec.order_line.filtered(
                lambda l: l.rental_state == 'active')
            if not active_lines:
                raise ValidationError(
                    _("No active rental items found to hire-off."))

            picking_type = self.env['stock.picking.type'].search(
                [('name', '=', 'Rental Physical Inventory')], limit=1)
            if not picking_type:
                raise ValidationError(
                    _("Operation type 'Rental Physical Inventory' not found. "
                      "Please contact your system administrator!"))

            try:
                picking = rec._create_hireoff_picking(picking_type)
                rec.write({
                    'state': 'hireoff',
                    'hireoff_date': fields.Datetime.now(),
                })
                active_lines.write({'rental_state': 'hireoff'})
                rec.message_post(
                    body=_("Rental order hired-off. Physical inventory: %s")
                    % picking.name)
            except Exception as e:
                _logger.error("Hire-off failed for %s: %s", rec.name, e)
                raise ValidationError(
                    _("Failed to process hire-off. Error: %s") % str(e))

    def action_view_rental_contract(self, contract_id):
        action = self.env['ir.actions.actions']._for_xml_id(
            'gdi_rental.action_gdi_rental_contracts_view')
        form_view = [(
            self.env.ref('gdi_rental.view_gdi_rental_contract_tree_form').id,
            'form')]
        action['views'] = form_view + [
            (s, v) for s, v in action.get('views', []) if v != 'form']
        action['res_id'] = contract_id.id
        return action

    def open_related_contract(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_id': self.contract_id.id or False,
            'res_model': 'rental.contract',
            'view_mode': 'form',
        }

    # -------------------------------------------------------------------------
    # CONTRACT PREPARATION
    # -------------------------------------------------------------------------

    def _prepare_contract_vals(self):
        self.ensure_one()
        return {
            'partner_id': self.partner_id.id,
            'pricelist_id': self.pricelist_id.id,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': self.user_id.id,
            'order_id': self.id,
            'company_id': self.company_id.id,
            'currency_id': self.currency_id.id,
            'fiscal_position_id': self.fiscal_position_id.id,
            'contract_line_ids': [],
        }

    def _prepare_rental_contract_vals(self):
        """Prepare contract vals including rental period info."""
        self.ensure_one()
        return {
            'order_id': self.id,
            'partner_id': self.partner_id.id,
            'customer_reference': self.customer_reference or '',
            'customer_po_number': self.customer_po_number or '',
            'duration': self.duration or 1,
            'duration_unit': self.duration_unit or 'month',
            'start_date': self.start_date or False,
            'end_date': self.end_date or False,
            'pricelist_id': self.pricelist_id.id,
            'fiscal_position_id': self.fiscal_position_id.id,
        }

    def _prepare_contract_line(self, line):
        vals = {
            'ro_line_id': line.id,
            'name': line.name,
            'item_type': line.item_type,
            'item_code': line.item_code,
            'product_id': line.product_id.id or False,
            'product_uom': line.product_uom.id or False,
            'product_uom_qty': line.product_uom_qty,
            'product_uom_txt': line.product_uom_txt or '',
            'price_unit': line.price_unit,
            'tax_id': line.tax_id.ids or False,
            'duration': line.duration,
            'duration_unit': line.duration_unit,
        }
        if line.item_type == 'set' and line.component_line_ids:
            vals['component_line_ids'] = [
                (0, 0, {
                    'product_id': c.product_id.id,
                    'name': c.name or '',
                    'price_unit': c.price_unit,
                    'product_uom_qty': c.product_uom_qty,
                    'product_uom': c.product_uom.id,
                }) for c in line.component_line_ids
            ]
        return vals

    # -------------------------------------------------------------------------
    # HIRE-OFF: STOCK PICKING CREATION
    # -------------------------------------------------------------------------

    def _create_hireoff_picking(self, picking_type):
        """Create physical inventory picking for hire-off."""
        self.ensure_one()
        move_lines = self._build_hireoff_moves(picking_type)
        if not move_lines:
            raise UserError(_("No stock moves could be created for hire-off."))

        now = fields.Datetime.now()
        picking = self.env['stock.picking'].create({
            'partner_id': self.partner_id.id,
            'contact_person_id': self.partner_id.id,
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_dest_id.id,
            'location_dest_id': picking_type.default_location_src_id.id,
            'move_type': 'direct',
            'scheduled_date': now,
            'date_deadline': now,
            'origin': f"{self.name} - Hire-off (IN)",
            'customer_po': self.customer_po_number,
            'src_user_id': self.env.user.id,
            'move_ids_without_package': move_lines,
            'rental_id': self.id,
        })
        picking.button_validate()
        return picking

    def _build_hireoff_moves(self, picking_type):
        """Build stock move data for all active lines."""
        now = fields.Datetime.now()
        moves = []
        seq = 0

        for line in self.order_line.filtered(lambda l: l.rental_state == 'active'):
            seq += 1
            prev_move = self._get_prev_move(line)
            if not prev_move:
                _logger.warning("No previous move for hire-off line: %s", line.name)
                continue

            if line.item_type == 'set' and line.component_line_ids:
                moves.extend(self._build_set_hireoff_moves(
                    line, picking_type, prev_move, seq, now))
            else:
                move = self._build_unit_hireoff_move(
                    line, picking_type, prev_move, seq, now)
                if move:
                    moves.append(move)
        return moves

    def _get_prev_move(self, line):
        """Get the most recent stock move for a line."""
        if not line.stock_move_ids:
            return False
        return line.stock_move_ids.sorted(
            key=lambda m: m.date, reverse=True)[:1]

    def _build_unit_hireoff_move(self, line, picking_type, prev_move, seq, now):
        """Build hireoff move for a UNIT line."""
        move_line_vals = self._build_hireoff_move_lines(
            line.product_id, line.product_uom, prev_move, picking_type, now)
        return (0, 0, {
            'sequence_number': seq,
            'name': line.name,
            'description_picking': line.name,
            'product_id': line.product_id.id,
            'product_uom': line.product_uom.id,
            'product_uom_qty': line.product_uom_qty,
            'date': now,
            'location_id': picking_type.default_location_dest_id.id,
            'location_dest_id': prev_move.location_id.id,
            'move_line_ids': move_line_vals,
        })

    def _build_set_hireoff_moves(self, line, picking_type, prev_move, seq, now):
        """Build hireoff moves for all components of a SET line."""
        moves = []
        picking = prev_move.picking_id if prev_move._name == 'stock.move' else prev_move

        for comp_seq, comp in enumerate(line.component_line_ids, 1):
            comp_prev = picking.move_lines.filtered(
                lambda m: m.product_id.id == comp.product_id.id)[:1]
            if not comp_prev:
                _logger.warning(
                    "No previous move for component %s in set %s",
                    comp.product_id.name, line.name)
                continue

            move_line_vals = self._build_hireoff_move_lines(
                comp.product_id, comp.product_uom, comp_prev, picking_type, now)
            product = comp.product_id
            name = product.product_name or product.name or comp.name
            moves.append((0, 0, {
                'sequence_number': seq + (comp_seq * 0.01),
                'name': f"{line.name} - {name}",
                'description_picking': name,
                'product_id': product.id,
                'product_uom': comp.product_uom.id,
                'product_uom_qty': comp.product_uom_qty,
                'date': now,
                'location_id': picking_type.default_location_dest_id.id,
                'location_dest_id': comp_prev.location_id.id,
                'move_line_ids': move_line_vals,
            }))
        return moves

    def _build_hireoff_move_lines(self, product, uom, prev_move, picking_type, now):
        """Build detailed move lines with lot tracking from previous move."""
        lines = []
        if not prev_move.move_line_ids:
            return lines
        for ml in prev_move.move_line_ids:
            lines.append((0, 0, {
                'product_id': product.id,
                'product_uom_id': uom.id,
                'product_uom_qty': ml.qty_done,
                'qty_done': ml.qty_done,
                'date': now,
                'location_id': picking_type.default_location_dest_id.id,
                'location_dest_id': prev_move.location_id.id,
                'lot_id': ml.lot_id.id if ml.lot_id else False,
            }))
        return lines