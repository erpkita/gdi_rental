# -*- coding: utf-8 -*-

from datetime import timedelta

from odoo import api, fields, models, _


class GDIRentalDashboard(models.AbstractModel):
    _name = 'gdi.rental.dashboard'
    _description = 'GDI Rental Dashboard Data'

    @api.model
    def get_dashboard_data(self):
        today = fields.Date.today()
        soon = today + timedelta(days=30)

        RQ = self.env['gdi.rental.quotation']
        RO = self.env['gdi.rental.order']
        RC = self.env['rental.contract']

        # --- KPI counts ---
        draft_rq = RQ.search_count([('state', 'in', ['draft', 'sent'])])
        confirmed_ro = RO.search_count([('state', '=', 'confirm')])
        active_contracts = RC.search_count([('state', '=', 'signed')])
        expiring_soon = RC.search_count([
            ('state', '=', 'signed'),
            ('end_date', '<=', soon),
            ('end_date', '>=', today),
        ])
        ongoing_ro = RO.search_count([('state', '=', 'ongoing')])
        total_items_on_rent = sum(
            RO.search([('state', '=', 'ongoing')]).mapped(
                lambda o: len(o.order_line.filtered(
                    lambda l: l.rental_state == 'active'))))

        # --- Overdue contracts (end_date passed but still signed) ---
        overdue_recs = RC.search([
            ('state', '=', 'signed'),
            ('end_date', '<', today),
        ], order='end_date asc', limit=10)
        overdue_list = []
        for rc in overdue_recs:
            days_overdue = (today - rc.end_date).days if rc.end_date else 0
            overdue_list.append({
                'id': rc.id,
                'name': rc.name or '',
                'partner': rc.partner_id.name or '',
                'end_date': fields.Date.to_string(rc.end_date),
                'days_overdue': days_overdue,
                'items': len(rc.contract_line_ids),
            })

        # --- Expiring contracts list ---
        expiring_recs = RC.search([
            ('state', '=', 'signed'),
            ('end_date', '<=', soon),
            ('end_date', '>=', today),
        ], order='end_date asc', limit=10)
        expiring_list = []
        for rc in expiring_recs:
            days_left = (rc.end_date - today).days if rc.end_date else 0
            expiring_list.append({
                'id': rc.id,
                'name': rc.name or '',
                'partner': rc.partner_id.name or '',
                'end_date': fields.Date.to_string(rc.end_date),
                'days_left': days_left,
            })

        # --- Recent rental orders ---
        recent_ros = RO.search([], order='date_order desc, id desc', limit=8)
        recent_list = []
        for ro in recent_ros:
            state_map = {
                'confirm': 'Confirmed',
                'ongoing': 'Ongoing',
                'hireoff': 'Hired-Off',
                'cancel': 'Cancelled',
            }
            recent_list.append({
                'id': ro.id,
                'name': ro.name or '',
                'partner': ro.partner_id.name or '',
                'state': ro.state,
                'state_label': state_map.get(ro.state, ro.state),
                'date': fields.Datetime.to_string(ro.date_order) if ro.date_order else '',
                'line_count': len(ro.order_line),
            })

        # --- Pending deliveries (RDO not yet done) ---
        DO = self.env['stock.picking']
        pending_dos = DO.search([
            ('is_rental_do', '=', True),
            ('state', 'not in', ['done', 'cancel']),
        ], order='scheduled_date asc', limit=10)
        pending_list = []
        state_label_map = {
            'draft': 'Draft',
            'waiting': 'Waiting',
            'confirmed': 'Waiting',
            'assigned': 'Ready',
        }
        for do in pending_dos:
            sched = do.scheduled_date.date() if do.scheduled_date else False
            days_until = (sched - today).days if sched else 0
            pending_list.append({
                'id': do.id,
                'name': do.name or '',
                'partner': do.partner_id.name or '',
                'ro_name': do.gdi_rental_id.name or '',
                'scheduled_date': fields.Date.to_string(sched) if sched else '',
                'items': len(do.rental_order_item_ids),
                'days_until': days_until,
                'state': do.state,
                'state_label': state_label_map.get(do.state, do.state),
            })

        return {
            'kpi': {
                'draft_rq': draft_rq,
                'confirmed_ro': confirmed_ro,
                'active_contracts': active_contracts,
                'expiring_soon': expiring_soon,
                'ongoing_ro': ongoing_ro,
                'items_on_rent': total_items_on_rent,
                'overdue': len(overdue_list),
            },
            'overdue_contracts': overdue_list,
            'expiring_contracts': expiring_list,
            'recent_orders': recent_list,
            'pending_deliveries': pending_list,
        }
