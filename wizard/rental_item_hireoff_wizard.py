# -*- coding: utf-8 -*-

from odoo import models, fields, api, _
from odoo.exceptions import ValidationError

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
    dest_location_id = fields.Many2one("stock.location", string="Dest. Location", required=True)
    rental_orderline_id = fields.Many2one("gdi.rental.order.line", string="Rental Item")
    reason = fields.Text(string="Reason", required=True)


    def action_confirm(self):
        pass