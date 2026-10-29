"""Turn an ExtractedDocument into editable production rows.

* Ditto marks and blank cells inherit from the previous row (or the
  document header) for fields where that is normal; the row is flagged so the
  operator sees where the value came from.
* Low-confidence / uncertain values are flagged and block printing until
  the operator confirms or corrects them.
* Original OCR text is preserved separately from the normalized value.
"""

from __future__ import annotations

import re
from typing import Optional

from vspm.domain.models import (
    INHERITABLE_FIELDS, ROW_FIELDS, ExtractedDocument, ExtractedValue, ProductionRow, RowStatus,
)
from vspm.services.customers import CustomerService, clean_display

CONFIDENCE_THRESHOLD = 0.80

FIELD_TO_ROW_ATTR = {
    "customer": "customer_full",
    "material": "material",
    "thickness": "thickness",
    "dimensions": "dimensions",
    "weight": "weight",
    "pieces": "pieces",
    "packages": "packages",
    "source_barcode": "source_barcode",
    "source_roll": "source_roll",
    "location": "location",
    "document_date": "document_date",
}

NUMERIC_FIELDS = {"weight", "pieces", "packages"}


def normalize_value(field: str, raw: str) -> str:
    v = clean_display(raw)
    if field == "weight":
        v = re.sub(r"(?i)\s*(kg|κιλα|κιλά|κ)\.?$", "", v)
        v = v.replace(" ", "")
    elif field in ("pieces", "packages"):
        v = re.sub(r"(?i)\s*(τεμ|τεμ\.|τεμαχια)\.?$", "", v).replace(" ", "")
    elif field == "thickness":
        v = v.replace(" ", "")
    elif field == "dimensions":
        v = v.upper()
    return v


def rows_from_extraction(doc: ExtractedDocument, customers: CustomerService,
                         threshold: float = CONFIDENCE_THRESHOLD) -> list[ProductionRow]:
    out: list[ProductionRow] = []
    prev: dict[str, tuple[Optional[str], bool]] = {}  # field -> (value, uncertain)
    for idx, er in enumerate(sorted(doc.rows, key=lambda r: (r.page, r.row_number)), start=1):
        row = ProductionRow(row_index=idx, status=RowStatus.ocr_complete)
        for field in ROW_FIELDS:
            ev: ExtractedValue = getattr(er, field)
            header: Optional[ExtractedValue] = getattr(doc, field, None) if field in INHERITABLE_FIELDS else None
            value: Optional[str] = None
            uncertain = ev.uncertain or (ev.raw is not None and ev.confidence < threshold)
            source = "row"
            if ev.raw is not None and not ev.is_ditto:
                value = normalize_value(field, ev.raw)
            elif ev.is_ditto and field in prev and prev[field][0]:
                value, prev_unc = prev[field]
                uncertain = uncertain or prev_unc or field in NUMERIC_FIELDS
                source = "ditto"
            elif ev.is_ditto:
                uncertain, source = True, "ditto_without_previous"
            elif header is not None and header.raw is not None:
                value = normalize_value(field, header.raw)
                uncertain = uncertain or header.uncertain or header.confidence < threshold
                source = "header"
            elif field in INHERITABLE_FIELDS and field in prev and prev[field][0] and field != "customer":
                # Repeated heading omitted on later rows: carry previous value but make the operator confirm it.
                value, _ = prev[field]
                uncertain, source = True, "carried_forward"

            row.ocr_original[field] = ev.raw if ev.raw is not None else (
                header.raw if source == "header" and header is not None else None)
            setattr(row, FIELD_TO_ROW_ATTR[field], value or "")
            if uncertain:
                row.flags[FIELD_TO_ROW_ATTR[field]] = "uncertain"
            elif source in ("ditto", "header"):
                row.flags[FIELD_TO_ROW_ATTR[field]] = f"info:{source}"
            note = ev.note or (header.note if source == "header" and header is not None else None)
            if note:
                row.flags[f"note:{FIELD_TO_ROW_ATTR[field]}"] = note
            prev[field] = (value, uncertain)

        sel = customers.select(row.customer_full)
        row.template_suggested = sel.template_key
        row.template_key = sel.template_key
        row.template_reason = sel.reason
        row.template_needs_confirmation = sel.needs_confirmation
        row.customer_short = sel.short_name
        if not sel.short_name_mapped and row.customer_full:
            row.flags.setdefault("customer_short", "info:unmapped")
        if any(v == "uncertain" for v in row.flags.values()) or sel.needs_confirmation:
            row.status = RowStatus.needs_review
        out.append(row)
    return out
