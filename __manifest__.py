# -*- coding: utf-8 -*-
#############################################################################
#
#    Rivan Rivaldi Nugraha.
#
#    Copyright (C) 2025-TODAY Rivan Rivaldi N (<https://id.linkedin.com/in/rivan-rivaldi-nugraha-2b727b1b8>)
#    Author: Rivan Rivaldi N (<https://id.linkedin.com/in/rivan-rivaldi-nugraha-2b727b1b8>)
#
#############################################################################

{
    'name': 'GDI- Rental Module',
    'version': '1.0',
    'category': 'ERP',
    'summary': """
        Rental module for PT. Great Dynamic Indonesia.
        Involving the following process:
        - RQ, RO, Contract, DO, Invoice and Sales module as well.
    """,
    'author': 'Rivan Rivaldi',
    'maintainer': 'Rivan Rivaldi',
    'depends': [
        'base',
        'gdi_erp_dev_v15'
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/gdi_rental_sequence.xml',
        # 'data/gdi_rental_picking_type.xml',
        'report/rental_quotation_templates.xml',
        'report/rental_reports.xml',
        'wizard/views/rental_contract_creation_wizard_views.xml',
        'views/product_views.xml',
        'views/rental_quotation_views.xml',
        'views/rental_order_views.xml',
        'views/rental_contract_views.xml',
        'views/rental_delivery_order_views.xml',
        'views/menu_views.xml',
    ],
    'license': 'LGPL-3',
    'installable': True,
    'auto_install': False,
    'application': True
}