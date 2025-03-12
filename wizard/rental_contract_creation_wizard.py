# -*- coding: utf-8 -*-

from odoo import models, fields, api, _

class RentalContractCreationWizard(models.TransientModel):
    _name = "rental.contract.creation.wizard"
    _description = "Rental Contract Creation Wizard"

    rental_id = fields.Many2one('gdi.rental.order', string="Rental No.")
    date_definition_level = fields.Selection([
        ('order', 'Rental Order Level'),
        ('item', 'Rental Order Item Level')
    ], string="Date Definition Level", default='order', required=True,
       help="Indicates whether the start and end dates are defined at the rental order level or at the rental order item level.")
    

    def action_create_contract(self):
        for rec in self:
            rental_id = rec.rental_id
            if rental_id:
                contract_id = rental_id.action_generate_contract()
                contract_id.write({
                    'date_definition_level': rec.date_definition_level
                })

                return rental_id.action_view_rental_contract(contract_id)
