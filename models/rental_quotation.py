# -*- coding: utf-8 -*-

import json
from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError
from odoo.tools import float_is_zero, html_keep_url, is_html_empty
from dateutil.relativedelta import relativedelta


class RentalQuotation(models.Model):
    _name = "rental.quotation"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Rental Quotation"
    _order = 'date_order, id desc'

    def _default_validity_date(self):
        if self.env['ir.config_parameter'].sudo().get_param('sale.use_quotation_validity_days'):
            days = self.env.company.quotation_validity_days
            if days > 0:
                return fields.Date.to_string(datetime.now() + timedelta(days))
        return False

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

    name = fields.Char(string="RQ Reference", 
                       require=True, copy=False, readonly=True, 
                       states={'draft': [('readonly', False)]}, index=True, 
                       default=lambda self: _('New'))
    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)
    date_order = fields.Datetime(string='Quotation Date', 
                                 required=True, readonly=True, index=True, 
                                 states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, 
                                 copy=False, 
                                 default=fields.Datetime.now, 
                                 help="Creation date of rental quotation")
    validity_date = fields.Date(string='Valid Until', readonly=True, copy=False, 
                                states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
                                default=_default_validity_date)
    is_expired = fields.Boolean(compute='_compute_is_expired', string="Is expired")
    create_date = fields.Datetime(string='Creation Date', 
                                  readonly=True, index=True, 
                                  help="Date on which quotation is created.")
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
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
        required=True, change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]",)
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address',
        readonly=True, required=True,
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)], 'sale': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",)
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address', readonly=True, required=True,
        states={'draft': [('readonly', False)], 'sent': [('readonly', False)], 'sale': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]",)
    
    pricelist_id = fields.Many2one(
        'product.pricelist', string='Pricelist', check_company=True,  # Unrequired company
        required=True, readonly=True, states={'draft': [('readonly', False)], 'sent': [('readonly', False)]},
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]", tracking=1,
        help="If you change the pricelist, only newly added lines will be affected.")
    currency_id = fields.Many2one(related='pricelist_id.currency_id', depends=["pricelist_id"], store=True, ondelete="restrict")

    order_line = fields.One2many('rental.quotation.line', 'quotation_id', 
                                 string="Order Lines",
                                  states={'cancel': [('readonly', True)], 'done': [('readonly', True)]}, 
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
        ('draft', 'Quotation'),
        ('sent', 'Quotation Sent'),
        ('confirm', 'Confirmed'),
        ('lock', 'Locked'),
        ('cancel', 'Cancelled')
    ], default='draft')

    rental_id = fields.Many2one("gdi.rental.order", string="Rental Order", readonly=True)

    start_date = fields.Date(string="Start Date", default=fields.Date.today)
    end_date = fields.Date(string="End Date", compute="_compute_end_date", store=True)
    duration = fields.Integer(string="Duration", default=1, required=True, compute="_compute_duration_from_lines", inverse="_inverse_duration", store=True)
    duration_unit = fields.Selection([
        ('hour', 'Hours'),
        ('day', 'Days'),
        ('week', 'weeks'),
        ('month', 'Months')
    ], string="Unit", default='month', required=True,
    compute="_compute_duration_from_lines",
    inverse="_inverse_duration",
    store=True
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
            vals['name'] = "RQ" + self.env['ir.sequence'].next_by_code('rental.quotation', sequence_date=seq_date) or _('New')        
        result = super(RentalQuotation, self).create(vals)
        return result

    def _compute_is_expired(self):
        today = fields.Date.today()
        for order in self:
            order.is_expired = order.state == 'sent' and order.validity_date and order.validity_date < today

    @api.depends('order_line.tax_id', 'order_line.price_unit', 'amount_total', 'amount_untaxed')
    def _compute_tax_totals_json(self):
        def compute_taxes(order_line):
            price = order_line.price_unit * (1 - (order_line.discount or 0.0) / 100.0)
            order = order_line.quotation_id
            return order_line.tax_id._origin.compute_all(price, order.currency_id, order_line.product_uom_qty, product=order_line.product_id, partner=order.partner_shipping_id)

        account_move = self.env['account.move']
        for order in self:
            tax_lines_data = account_move._prepare_tax_lines_data_for_totals_from_object(order.order_line, compute_taxes)
            tax_totals = account_move._get_tax_totals(order.partner_id, tax_lines_data, order.amount_total, order.amount_untaxed, order.currency_id)
            order.tax_totals_json = json.dumps(tax_totals)

    @api.depends('company_id.account_fiscal_country_id', 'fiscal_position_id.country_id', 'fiscal_position_id.foreign_vat')
    def _compute_tax_country_id(self):
        for record in self:
            if record.fiscal_position_id.foreign_vat:
                record.tax_country_id = record.fiscal_position_id.country_id
            else:
                record.tax_country_id = record.company_id.account_fiscal_country_id

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

    @api.onchange('partner_shipping_id', 'partner_id', 'company_id')
    def onchange_partner_shipping_id(self):
        """
        Trigger the change of fiscal position when the shipping address is modified.
        """
        self.fiscal_position_id = self.env['account.fiscal.position'].with_company(self.company_id).get_fiscal_position(self.partner_id.id, self.partner_shipping_id.id)
        return {}

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


    def action_cancel(self):
        for rec in self:
            rec.write({
                'state': 'Cancel'
            })
    
    def action_print_quotation(self):
        for rec in self:
            if rec.state == 'draft':
                rec.write({
                    'state': 'sent'
                })
            return rec.env.ref('gdi_rental.gdi_action_report_rental_quotation').report_action(rec)
    
    def action_send_quotation(self):
        for rec in self:
            rec.write({
                'state': 'sent'
            })

    def _prepare_rental_order(self):
        partner = self.partner_id
        order_vals = {
            'partner_id': partner.id or False,
            'partner_invoice_id': self.partner_invoice_id.id or False,
            'partner_shipping_id': self.partner_shipping_id.id or False,
            'pricelist_id': self.pricelist_id.id or False,
            'customer_reference': self.customer_reference or False,
            'customer_po_number': self.customer_po_number or False,
            'user_id': self.user_id.id or False,
            'quotation_id': self.id or False,
            'company_id': self.company_id.id or False,
            'note': self.note or False,
            'date_order': self.date_order,
            'currency_id': self.currency_id.id or False,
            'order_line' : [],
            'fiscal_position_id': self.fiscal_position_id.id,
            'duration': self.duration,
            'duration_unit': self.duration_unit,
            'start_date': self.start_date,
            'end_date': self.end_date,
        }

        return order_vals
    
    def _prepare_rental_order_line(self, line):
        orderline_vals = {
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
            'duration_unit': line.duration_unit,
            'start_date': line.start_date,
            'end_date': line.end_date
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

            orderline_vals.update({'component_line_ids': component_records})

        return orderline_vals
        
    def action_confirm(self):
        for rec in self:
            if not rec.customer_reference or not rec.customer_po_number:
                raise ValidationError(_("Please input Customer Reference and Customer Ref. PO !"))            
            order_vals = self._prepare_rental_order()
            rental_id = self.env['gdi.rental.order'].create(order_vals)
            for line in rec.order_line:
                orderline_values = self._prepare_rental_order_line(line)
                orderline_values.update({'order_id': rental_id.id})
                rec.env['gdi.rental.order.line'].create(orderline_values)
            
            rec.write({'state': 'confirm'})
            return rec.action_view_rental_orders(rental_id)
            
    def action_view_rental_orders(self, rental_id):
        action = self.env['ir.actions.actions']._for_xml_id("gdi_rental.action_gdi_rental_order")
        form_view = [(self.env.ref('gdi_rental.view_gdi_rental_order_form').id, 'form')]
        if 'views' in action:
            action['views'] = form_view + [(state,view) for state,view in action['views'] if view != 'form']
        else:
            action['views'] = form_view
        action['res_id'] = rental_id.id

        
        return action