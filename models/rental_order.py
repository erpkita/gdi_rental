# -*- coding: utf-8 -*-

from datetime import datetime, timedelta

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError

class GdiRentalOrder(models.Model):
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

    state = fields.Selection([
        ('confirm', 'Confirmed'),
        ('ongoing', 'Ongoing'),
        ('hireoff', 'Hired-off'),
        ('cancel', 'Cancelled')
    ], default='confirm')

    quotation_id = fields.Many2one("rental.quotation", string="Quotation", readonly=True)

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

    def action_cancel(self):
        pass

    def action_print_order(self):
        pass
    
    def action_start_rental(self):
        pass

    def action_hireoff(self):
        pass