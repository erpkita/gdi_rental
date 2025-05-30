# -*- coding: utf-8 -*-


from odoo import api, fields, models, _
from odoo.exceptions import UserError, ValidationError
from dateutil.relativedelta import relativedelta

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
        ('signed', 'Active'),
        ('done', 'Done'),
        ('cancel', 'Cancelled')
    ], string="Status", default='draft')
    
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
        for rec in self:
            picking_type_id = self.env['stock.picking.type'].search([('name', '=', 'Rental Delivery Orders')], limit=1)
            if not picking_type_id:
                picking_type_id = self.env['stock.picking.type'].search([('name', '=', 'Delivery Orders')], limit=1)
            warehouse_id = picking_type_id.warehouse_id
            stock_picking_vals = {
                'partner_id': rec.partner_id.id or False,
                'contact_person_id': rec.partner_id.id or False,
                'is_rental_do': True,
                'gdi_rental_id': rec.order_id.id or False,
                'rental_contract_id': rec.id or False,
                'picking_type_id': picking_type_id.id or False,
                'origin': rec.name,
                'customer_po': rec.customer_po_number,
                'move_type': 'direct',
                'location_id': picking_type_id.default_location_src_id.id or False,
                'location_dest_id': rec.partner_id.property_stock_customer.id or False,
                'src_user_id': rec.user_id.id or False,
                'scheduled_date': fields.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'date_deadline': fields.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                'date_done': fields.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            rental_item_ids = []
            move_line_ids = []
            for item in rec.contract_line_ids:
                rental_item_ids.append((
                    0, 0, {
                        'name': item.name or '',
                        'item_code': item.item_code or '',
                        'sequence': item.sequence or '',
                        'product_id': item.product_id.id or False,
                        'product_template_id': item.product_template_id.id or False,
                        'product_uom_qty': item.product_uom_qty or 1.0,
                        'product_uom': item.product_uom.id or False,
                        'product_uom_category_id': item.product_uom_category_id.id or False,
                        'product_uom_txt': item.product_uom_txt or 'SET',
                        'price_unit': item.price_unit or 0.0,
                        'start_date': item.start_date or False,
                        'end_date': item.end_date or False,
                        'duration': item.duration or False,
                        'duration_unit': item.duration_unit or False,
                    }
                ))

                if item.item_type != 'set':
                    move_line_ids.append((0,0, {
                        'sequence_number': item.sequence,
                        'name': item.name,
                        'description_picking': item.name,
                        'product_id': item.product_id.id or False,
                        'product_uom': item.product_uom.id or False,
                        'location_id':picking_type_id.default_location_src_id.id or False,
                        'location_dest_id': rec.partner_id.property_stock_customer.id or False,
                        'date': fields.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        'price_unit': item.price_unit,
                        'product_uom_qty': item.product_uom_qty
                    }))
                else:
                    for comp in item.component_line_ids:
                        move_line_ids.append((0,0, {
                            'sequence_number': item.sequence,
                            'name': comp.product_id.product_name,
                            'description_picking': comp.product_id.product_name,
                            'product_id': comp.product_id.id or False,
                            'product_uom': comp.product_uom.id or False,
                            'location_id':picking_type_id.default_location_src_id.id or False,
                            'location_dest_id': rec.partner_id.property_stock_customer.id or False,
                            'date': fields.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            'price_unit': comp.price_unit,
                            'product_uom_qty': comp.product_uom_qty
                        }))

                item.ro_line_id.update({
                    'rental_state': 'active'
                })

            stock_picking_vals.update({
                'move_ids_without_package': move_line_ids,
                'rental_order_item_ids': rental_item_ids})
            
            picking_id = self.env['stock.picking'].create(stock_picking_vals)

            rec.write({
                'state': 'signed'
            })