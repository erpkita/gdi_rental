odoo.define('gdi_rental.rental_set_widget', function (require) {
    'use strict';

    var Widget = require('web.Widget');
    var widgetRegistry = require('web.widget_registry');
    var FormViewDialog = require('web.view_dialogs').FormViewDialog;

    /**
     * Widget that opens a Rental Set creation form WITHOUT triggering
     * the parent form's save/validation cycle.
     *
     * When the set is saved in the dialog, it automatically fills the
     * rental_set_id field on the parent record via field_changed event,
     * which also triggers the Python onchange to populate item_code,
     * description, and the component list.
     */
    var OpenRentalSets = Widget.extend({
        tagName: 'span',
        events: {
            'click .o_rental_set_btn': '_onOpenSets',
        },

        init: function (parent, state) {
            this._super(parent);
            this.record = state;
        },

        start: function () {
            this._super.apply(this, arguments);
            this.$el.html(
                '<button type="button" ' +
                'class="btn btn-link p-0 o_rental_set_btn" ' +
                'title="Create New Rental Set">' +
                '<i class="fa fa-plus-circle"></i>' +
                '</button>'
            );
        },

        _onOpenSets: function (ev) {
            ev.stopPropagation();
            ev.preventDefault();
            var self = this;

            new FormViewDialog(self, {
                res_model: 'gdi.rental.set',
                title: 'Create Rental Set',
                disable_multiple_selection: true,
                on_saved: function (record) {
                    // Auto-fill rental_set_id with the newly created set
                    self.trigger_up('field_changed', {
                        dataPointID: self.record.id,
                        changes: {
                            rental_set_id: {
                                id: record.res_id,
                                display_name: record.data.display_name || record.data.name || '',
                            },
                        },
                    });
                },
            }).open();
        },
    });

    widgetRegistry.add('open_rental_sets', OpenRentalSets);
    return OpenRentalSets;
});
