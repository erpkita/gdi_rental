odoo.define('gdi_rental.Dashboard', function (require) {
    'use strict';

    var AbstractAction = require('web.AbstractAction');
    var core = require('web.core');
    var QWeb = core.qweb;

    var RentalDashboard = AbstractAction.extend({
        template: 'gdi_rental.DashboardMain',
        cssLibs: [],
        jsLibs: [],
        events: {
            'click .o_dashboard_action': '_onActionClick',
            'click .o_dashboard_record': '_onRecordClick',
            'click .o_shortcut_action': '_onShortcutClick',
        },

        init: function (parent, action) {
            this._super.apply(this, arguments);
            this.dashboardData = {};
        },

        willStart: function () {
            var self = this;
            return this._super.apply(this, arguments).then(function () {
                return self._fetchDashboardData();
            });
        },

        start: function () {
            var self = this;
            return this._super.apply(this, arguments).then(function () {
                self._renderDashboard();
            });
        },

        // ---------------------------------------------------------------------
        // Private
        // ---------------------------------------------------------------------

        _fetchDashboardData: function () {
            var self = this;
            return this._rpc({
                model: 'gdi.rental.dashboard',
                method: 'get_dashboard_data',
                args: [],
            }).then(function (result) {
                self.dashboardData = result;
            });
        },

        _renderDashboard: function () {
            var $content = $(QWeb.render('gdi_rental.DashboardContent', {
                data: this.dashboardData,
            }));
            this.$('.o_rental_dashboard_content').empty().append($content);
        },

        _refreshDashboard: function () {
            var self = this;
            this._fetchDashboardData().then(function () {
                self._renderDashboard();
            });
        },

        // ---------------------------------------------------------------------
        // Handlers
        // ---------------------------------------------------------------------

        _onActionClick: function (ev) {
            ev.preventDefault();
            var $target = $(ev.currentTarget);
            var actionName = $target.data('action');
            var today = new Date().toISOString().split('T')[0];
            var soon = new Date(Date.now() + 30 * 86400000).toISOString().split('T')[0];
            var actionMap = {
                'draft_rq': {
                    name: 'Draft Quotations',
                    res_model: 'gdi.rental.quotation',
                    domain: [['state', 'in', ['draft', 'sent']]],
                },
                'confirmed_ro': {
                    name: 'Confirmed Rental Orders',
                    res_model: 'gdi.rental.order',
                    domain: [['state', '=', 'confirm']],
                },
                'active_contracts': {
                    name: 'Active Contracts',
                    res_model: 'rental.contract',
                    domain: [['state', '=', 'signed']],
                },
                'ongoing_ro': {
                    name: 'Ongoing Rentals',
                    res_model: 'gdi.rental.order',
                    domain: [['state', '=', 'ongoing']],
                },
                'overdue_contracts': {
                    name: 'Overdue Contracts',
                    res_model: 'rental.contract',
                    domain: [['state', '=', 'signed'], ['end_date', '<', today]],
                },
                'expiring_soon': {
                    name: 'Expiring Soon (< 30 days)',
                    res_model: 'rental.contract',
                    domain: [['state', '=', 'signed'], ['end_date', '>=', today], ['end_date', '<=', soon]],
                },
            };

            var action = actionMap[actionName];
            if (action) {
                this.do_action({
                    type: 'ir.actions.act_window',
                    name: action.name,
                    res_model: action.res_model,
                    view_mode: 'tree,form',
                    views: [[false, 'list'], [false, 'form']],
                    domain: action.domain,
                    target: 'current',
                });
            }
        },

        _onRecordClick: function (ev) {
            ev.preventDefault();
            var $target = $(ev.currentTarget);
            var resModel = $target.data('model');
            var resId = $target.data('id');
            if (resModel && resId) {
                this.do_action({
                    type: 'ir.actions.act_window',
                    res_model: resModel,
                    res_id: parseInt(resId),
                    views: [[false, 'form']],
                    target: 'current',
                });
            }
        },

        _onShortcutClick: function (ev) {
            ev.preventDefault();
            var shortcut = $(ev.currentTarget).data('shortcut');
            var shortcutMap = {
                'new_rq': {
                    type: 'ir.actions.act_window',
                    name: 'New Rental Quotation',
                    res_model: 'gdi.rental.quotation',
                    views: [[false, 'form']],
                    target: 'current',
                },
                'all_rq': {
                    type: 'ir.actions.act_window',
                    name: 'Rental Quotations',
                    res_model: 'gdi.rental.quotation',
                    view_mode: 'tree,form',
                    views: [[false, 'list'], [false, 'form']],
                    target: 'current',
                },
                'all_ro': {
                    type: 'ir.actions.act_window',
                    name: 'Rental Orders',
                    res_model: 'gdi.rental.order',
                    view_mode: 'tree,form',
                    views: [[false, 'list'], [false, 'form']],
                    target: 'current',
                },
                'all_contracts': {
                    type: 'ir.actions.act_window',
                    name: 'Contracts',
                    res_model: 'rental.contract',
                    view_mode: 'tree,form',
                    views: [[false, 'list'], [false, 'form']],
                    target: 'current',
                },
            };
            var action = shortcutMap[shortcut];
            if (action) {
                this.do_action(action);
            }
        },
    });

    core.action_registry.add('gdi_rental.dashboard', RentalDashboard);
    return RentalDashboard;
});
