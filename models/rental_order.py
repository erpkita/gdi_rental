# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class GdiRentaQuotation(models.Model):
    _name = "gdi.rental.order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "GDI Rental Order"
    _order = 'date_order, id desc'

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

    state = fields.Selection([
        ('confirm', 'Confirmed'),
        ('ongoing', 'Ongoing'),
        ('hireoff', 'Hired-off'),
        ('cancel', 'Cancelled')
    ], default='confirm')