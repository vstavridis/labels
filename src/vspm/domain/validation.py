"""Validation rules that gate printing."""

from __future__ import annotations

import re

from vspm.domain.models import ProductionRow, TemplateKey

NUMBER_RE = re.compile(r"^\d+([.,]\d+)?$")
# Pieces may be composite like "2+1" (existing workbook convention).
PIECES_RE = re.compile(r"^\d+(\s*\+\s*\d+)*$")
CODE39_RE = re.compile(r"^[0-9A-Z\-. $/+%]+$")


def required_fields(template: TemplateKey | None) -> list[str]:
    base = ["material", "thickness", "dimensions", "pieces", "weight"]
    # Every template except PG0 shows ΠΕΛΑΤΗΣ on the label; Excel always needs it.
    base.append("customer_short")
    return base


def validate_row(row: ProductionRow) -> dict[str, str]:
    """Return {field: problem}. Empty dict means printable."""
    errors: dict[str, str] = {}
    if row.template_key is None:
        errors["template_key"] = "required"
    for f in required_fields(row.template_key):
        if not str(getattr(row, f) or "").strip():
            errors[f] = "required"
    if row.weight and not NUMBER_RE.match(row.weight.strip()):
        errors["weight"] = "must be a number"
    if row.thickness and not NUMBER_RE.match(row.thickness.strip()):
        if row.thickness.strip() not in {"(-)", "-"}:
            errors["thickness"] = "must be a number like 0,60"
    if row.pieces and not PIECES_RE.match(row.pieces.strip()):
        errors["pieces"] = "must be a whole number (or e.g. 2+1)"
    if row.template_needs_confirmation:
        errors["template_key"] = "label type must be confirmed by the operator"
    for f, flag in row.flags.items():
        if flag == "uncertain" and f not in errors:
            errors[f] = "uncertain OCR value - confirm or correct"
    return errors
