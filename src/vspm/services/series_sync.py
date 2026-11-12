"""Barcode series set automatically: continue after the last barcode already used.

Before printing, the next number of every series (VS, PG, PG0, EX) is brought up to

    max( COUNTER of the DYMO template file (OneDrive),
         highest barcode of that series in the production workbook, column H, + 1,
         highest barcode the app itself has issued + 1 )

so a label printed by hand with DYMO Label in the meantime (and entered in the workbook, or
saved in the template file) is never issued again. The next number only ever moves FORWARD; a
series another PC has already moved further is left alone. Nothing is written to the workbook
or to the template files.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from vspm.domain.models import TemplateKey

log = logging.getLogger(__name__)


@dataclass
class SeriesSyncResult:
    set: dict[str, str] = field(default_factory=dict)       # series -> next barcode now
    sources: dict[str, str] = field(default_factory=dict)   # series -> where the number came from
    warnings: list[str] = field(default_factory=list)


def _parse(code: str) -> Optional[tuple[str, int]]:
    from vspm_registry.core import BARCODE_RE
    m = BARCODE_RE.fullmatch(str(code).strip().upper())
    return (m.group(1), int(m.group(2))) if m else None


def workbook_barcodes(ctx) -> tuple[list[str], Optional[str]]:
    """Column H of the production workbook (read-only). Returns (barcodes, problem or None)."""
    from vspm.excel.xlsx_package import SheetEditor, XlsxPackage
    s = ctx.settings
    data = None
    try:
        if s.excel_mode.value == "onedrive" and getattr(ctx, "cloud", None) is not None:
            data = ctx.cloud.workbook.read_bytes()
        elif ctx.writer is not None and ctx.writer.path.exists():
            data = ctx.writer.path.read_bytes()
    except Exception as e:  # noqa: BLE001 - reported, the other sources still count
        return [], f"Excel: {e}"
    if data is None:
        return [], None
    try:
        ed = SheetEditor(XlsxPackage(data), s.excel_sheet)
        return [str(v) for r, v in ed.column_values("H").items() if r > 1 and str(v).strip()], None
    except Exception as e:  # noqa: BLE001
        return [], f"Excel: {e}"


def sync_series(ctx, user: str) -> SeriesSyncResult:
    out = SeriesSyncResult()
    alloc = ctx.allocator
    if not (hasattr(alloc, "sequences") and hasattr(alloc, "set_sequence")):
        return out  # mock / local test ranges / remote registry: nothing to set here
    known = {q["series"]: q for q in alloc.sequences()}

    highest: dict[str, int] = {}
    codes, problem = workbook_barcodes(ctx)
    if problem:
        out.warnings.append(problem)
    for code in codes:
        p = _parse(code)
        if p:
            highest[p[0]] = max(highest.get(p[0], 0), p[1])
    for r in ctx.db.query("SELECT barcode FROM labels"):
        p = _parse(r["barcode"])
        if p:
            highest[p[0]] = max(highest.get(p[0], 0), p[1])

    for key in TemplateKey:
        try:
            spec = ctx.templates.spec(key)
        except Exception:  # noqa: BLE001 - template not configured
            continue
        prefix = spec.barcode_prefix
        counter = None
        try:
            counter = ctx.templates.load(key).counter_info(spec.counter_object)
        except Exception:  # noqa: BLE001 - template file not available yet
            pass
        candidates = {}
        if counter and counter.get("current"):
            candidates["DYMO"] = int(counter["current"])
        if prefix in highest:
            candidates["Excel"] = highest[prefix] + 1  # workbook column H or labels issued by the app
        cur = known.get(prefix)
        if not candidates:
            continue  # nothing to go on: never guess a number
        source, target = max(candidates.items(), key=lambda kv: kv[1])
        if cur is not None and target <= int(cur["next_value"]):
            continue  # already at or past it
        width = int(cur["format_width"]) if cur else int((counter or {}).get("format_width") or 3)
        try:
            alloc.set_sequence(prefix, prefix, target, format_width=width, operator=user)
        except Exception as e:  # noqa: BLE001 - e.g. another PC moved it further meanwhile
            log.info("series %s not moved to %s: %s", prefix, target, e)
            continue
        out.set[prefix] = f"{prefix}{str(target).zfill(width)}"
        out.sources[prefix] = source
        ctx.db.audit("barcode_series", prefix, "auto_set", {"next": target, "from": source,
                                                            "candidates": candidates}, user)
    return out
