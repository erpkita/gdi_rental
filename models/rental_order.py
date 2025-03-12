# -*- coding: utf-8 -*-
import json
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

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

    start_date = fields.Datetime(string="Start Date")
    end_date = fields.Datetime(string="End Date")

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
            if rec.date_definition_level == "order":
                rec._order_check_rental_period()
            else:
                for line in rec.order_line:
                    line.check_rental_period()
            
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
    
    def action_start_rental(self):
        for rec in self:
            for line in rec.order_line:
                line.check_rental_period()

    def action_hireoff(self):
        pass