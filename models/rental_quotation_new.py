# # -*- coding: utf-8 -*-

import json
import logging

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta

_logger = logging.getLogger(__name__)

class GDIRentalQuotation(models.Model):
    _name = "gdi.rental.quotation"
    _description = "GDI Rental Quotation"
    _inherit = [
        'mail.thread', 'mail.activity.mixin'
    ]
    _order = 'date_order desc, id desc'

    name = fields.Char(
        string="RFQ Reference", required=True, copy=False,
        readonly=True, index=True,
        default=lambda self: _('New'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('sent', 'Sent'),
        ('confirm', 'Confirmed'),
        ('cancel', 'Cancelled'),
    ], string='Status', default='draft', tracking=True)

    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    date_order = fields.Datetime(
        string='Order Date', required=True, index=True, copy=False,
        default=fields.Datetime.now,
        help="Creation date of the rental quotation.")

    # Rental Period
    rental_start_date = fields.Date(
        string="Rental Start Date", compute='_compute_rental_dates', store=True, readonly=False)
    rental_end_date = fields.Date(
        string="Rental End Date", compute='_compute_rental_dates', store=True, readonly=False)
    rental_duration = fields.Float(
        string="Duration (Months)", compute='_compute_rental_duration', store=True, readonly=False)

    @api.depends('order_line.start_date', 'order_line.end_date')
    def _compute_rental_dates(self):
        for record in self:
            dates_start = record.order_line.mapped('start_date')
            dates_end = record.order_line.mapped('end_date')
            if dates_start:
                record.rental_start_date = min([d for d in dates_start if d])
            if dates_end:
                record.rental_end_date = max([d for d in dates_end if d])

    @api.depends('rental_start_date', 'rental_end_date')
    def _compute_rental_duration(self):
        for record in self:
            if record.rental_start_date and record.rental_end_date:
                delta = relativedelta(record.rental_end_date, record.rental_start_date)
                record.rental_duration = (delta.years * 12) + delta.months + (delta.days / 30.0)
            else:
                record.rental_duration = 0.0

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        change_default=True, index=True, tracking=1,
        domain="[('type', '!=', 'private'), ('company_id', 'in', (False, company_id))]")
    partner_invoice_id = fields.Many2one(
        'res.partner', string='Invoice Address',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    partner_shipping_id = fields.Many2one(
        'res.partner', string='Delivery Address',
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")

    customer_reference = fields.Char(string="Customer Reference", copy=False)
    customer_po_number = fields.Char(string="Customer Ref. PO", copy=False)

    order_line = fields.One2many(
        'gdi.rental.quotation.line', 'quotation_id',
        string='Quotation Lines', copy=True, auto_join=True)

    @api.model
    def create(self, vals):
        if vals.get('name', _('New')) == _('New'):
            vals['name'] = self.env['ir.sequence'].next_by_code(
                'gdi.rental.quotation') or _('New')
        return super().create(vals)

    def action_send(self):
        self.write({'state': 'sent'})

    def action_confirm(self):
        self.write({'state': 'confirm'})

    def action_cancel(self):
        self.write({'state': 'cancel'})

    def action_draft(self):
        self.write({'state': 'draft'})


class GDIRentalQuotationLine(models.Model):
    _name = "gdi.rental.quotation.line"
    _description = "GDI Rental Quotation Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10, index=True)

    quotation_id = fields.Many2one(
        'gdi.rental.quotation', string='Quotation', required=True,
        ondelete='cascade', index=True, auto_join=True)
    company_id = fields.Many2one(
        related='quotation_id.company_id', store=True,
        string='Company', readonly=True)

    # Type Selection
    line_type = fields.Selection([
        ('unit', 'Unit'),
        ('set', 'Set')
    ], string='Type', default='unit', required=True)

    rental_set_id = fields.Many2one('gdi.rental.set', string="Rental Set")
    item_code = fields.Char(string="Product Code")

    product_id = fields.Many2one(
        'product.product', string='Product',
        domain="[('sale_ok', '=', True), ('rent_ok', '=', True), ('company_id', 'in', (False, company_id))]")
    
    name = fields.Text(string='Description')

    product_uom_category = fields.Many2one(related='product_id.uom_id.category_id', string="UOM Category", store=True, readonly=True)
    product_uom = fields.Many2one(
        'uom.uom', string="Unit of Measure", domain="[('category_id', '=', product_uom_category)]")
    product_uom_qty = fields.Float(
        string="Quantity", default=1.0,
        digits='Product Unit of Measure')
    price_unit = fields.Float(string='Unit Price', digits='Product Price')

    @api.onchange('line_type')
    def _onchange_line_type(self):
        if self.line_type == 'set':
            self.product_id = False
            self.product_uom_qty = 1.0
        else:
            self.rental_set_id = False

    @api.onchange('rental_set_id')
    def _onchange_rental_set_id(self):
        if self.line_type == 'set' and self.rental_set_id:
            self.item_code = self.rental_set_id.code
            self.name = self.rental_set_id.name

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id and self.line_type == 'unit':
            self.name = self.product_id.display_name
            self.item_code = self.product_id.item_code_ref
            # self.product_uom_category = self.product_id.uom_id.category_id
            self.product_uom = self.product_id.uom_id
            self.price_unit = self.product_id.lst_price

    # Rental Period Fields
    start_date = fields.Date(string="Start Date", required=True)
    end_date = fields.Date(string="End Date", required=True)
    duration = fields.Float(
        string="Duration (Months)", compute='_compute_duration', store=True)

    # Stock Information
    virtual_available = fields.Float(
        related='product_id.virtual_available', string="Forecasted Qty", readonly=True)
    qty_available = fields.Float(
        related='product_id.qty_available', string="On Hand Qty", readonly=True)

    @api.depends('start_date', 'end_date')
    def _compute_duration(self):
        for line in self:
            if line.start_date and line.end_date:
                delta = relativedelta(line.end_date, line.start_date)
                # Calculate months: years * 12 + months + (days / 30)
                line.duration = (delta.years * 12) + delta.months + (delta.days / 30.0)
            else:
                line.duration = 0.0

    @api.onchange('quotation_id')
    def _onchange_quotation_id(self):
        if self.quotation_id:
            self.start_date = self.quotation_id.rental_start_date
            self.end_date = self.quotation_id.rental_end_date

    