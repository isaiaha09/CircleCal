from __future__ import annotations

from django.contrib.admin.helpers import ActionForm


class UnfoldActionForm(ActionForm):
    """Action form compatible with Unfold's Alpine-powered actions UI.

    Unfold's default `admin/actions.html` uses Alpine state `action` to control
    visibility of the "Run" button (`x-show="action"`).

    Django's default ActionForm does not emit Alpine bindings on the `<select>`.
    Adding `x-model="action"` lets Alpine track the selected action without
    overriding Unfold templates.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        action_field = self.fields.get("action")
        if not action_field:
            return

        widget = getattr(action_field, "widget", None)
        if not widget or not hasattr(widget, "attrs"):
            return

        # Bind the action dropdown to Unfold's Alpine state.
        widget.attrs.setdefault("x-model", "action")
