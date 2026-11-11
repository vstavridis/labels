"""Approve a row straight from the print page (the operator has checked the label preview)."""

from __future__ import annotations

from vspm.ui.common import T, flash, operator

# Errors the operator clears by checking the label: an unconfirmed AI value, template or approval.
APPROVABLE = "uncertain OCR value"


def approvable(errors: dict[str, str]) -> bool:
    return bool(errors) and all(k == "approved" or APPROVABLE in v or k == "template_key" and "confirm" in v
                                for k, v in errors.items())


def _approve(ctx, row_id: int) -> None:
    left = ctx.production.approve_row(row_id, operator())
    if left:
        flash("error", T("not_printable") + " " + "; ".join(f"{k}: {v}" for k, v in left.items()))
    else:
        flash("success", T("approved_here"))
