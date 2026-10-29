"""Turn an approved production row into label text and an Excel row.

Only transformations that are established by the reference files are
applied automatically; anything else is configurable and off by default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import yaml

from vspm.domain.models import ExcelRowPayload, LabelContent, ProductionRow, TemplateKey
from vspm.dymo.template import TemplateSpec
from vspm.services.customers import clean_display, normalize_key, script_profile


class TransformError(ValueError):
    pass


@dataclass
class Material:
    canonical: str
    en: Optional[str]
    aliases: list[str] = field(default_factory=list)


@dataclass
class DimensionRule:
    name: str
    pattern: str
    replace: str
    enabled: bool
    templates: list[str]


@dataclass
class Transforms:
    materials: list[Material]
    dimension_rules: list[DimensionRule]

    @classmethod
    def from_yaml(cls, path: Path) -> "Transforms":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else {}
        data = data or {}
        return cls(
            materials=[Material(m["canonical"], m.get("en"), list(m.get("aliases") or [])) for m in data.get("materials", [])],
            dimension_rules=[DimensionRule(**r) for r in data.get("dimension_rules", [])],
        )

    # ---- material ---------------------------------------------------------------
    def find_material(self, text: str) -> Optional[Material]:
        key = normalize_key(text)
        for m in self.materials:
            names = [m.canonical, *m.aliases, *([m.en] if m.en else [])]
            if key in {normalize_key(n) for n in names}:
                return m
        return None

    def canonical_material(self, text: str) -> str:
        m = self.find_material(text)
        return m.canonical if m else clean_display(text).upper()

    def label_material(self, text: str, spec: TemplateSpec) -> str:
        canonical = self.canonical_material(text)
        if spec.label_language != "en":
            return canonical
        if script_profile(canonical) in ("latin", "none"):
            return canonical  # e.g. MIXED, INOX, RAL 9003
        m = self.find_material(text)
        if m and m.en:
            return m.en
        raise TransformError(
            f"No established English name for material '{canonical}' (EX label). "
            "Type the English material on the row or add it to config/materials.yaml.")

    # ---- dimensions ----------------------------------------------------------------
    def label_dimensions(self, text: str, template: TemplateKey) -> str:
        t = re.sub(r"\s+", "", clean_display(text)).upper()
        for r in self.dimension_rules:
            if r.enabled and template.value in r.templates:
                t = re.sub(r.pattern, r.replace, t)
        return t


def _to_decimal(text: str) -> Optional[float]:
    t = (text or "").strip().replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def format_thickness(text: str, decimal_sep: str) -> str:
    """'0,6' -> '0,60' / '0.60' (two decimals, as in all reference samples)."""
    v = _to_decimal(text)
    if v is None:
        return clean_display(text)
    return f"{v:.2f}".replace(".", decimal_sep)


def excel_thickness(text: str) -> str:
    """Workbook convention: text with dot and two decimals ('0.60')."""
    return format_thickness(text, ".")


def clean_number_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def build_label_content(row: ProductionRow, spec: TemplateSpec, transforms: Transforms, barcode: str) -> LabelContent:
    if row.template_key is None:
        raise TransformError("Row has no label template")
    return LabelContent(
        template_key=row.template_key,
        material=transforms.label_material(row.material, spec),
        thickness=format_thickness(row.thickness, spec.thickness_decimal),
        dimensions=transforms.label_dimensions(row.dimensions, row.template_key),
        pieces=clean_number_text(row.pieces),
        customer_short=clean_display(row.customer_short),
        weight=clean_number_text(row.weight).replace(".", ","),
        barcode=barcode,
    )


def build_excel_payload(row: ProductionRow, content: LabelContent, spec: TemplateSpec, transforms: Transforms,
                        print_date: date, material_source: str = "canonical") -> ExcelRowPayload:
    material = (transforms.canonical_material(row.material) if material_source == "canonical" else content.material)
    return ExcelRowPayload(
        material=material,
        thickness=excel_thickness(row.thickness),
        dimensions=content.dimensions,
        pieces=content.pieces,
        weight=clean_number_text(row.weight),
        customer=content.customer_short,
        category=spec.excel_category,
        barcode=content.barcode,
        location=clean_display(row.location),
        roll=clean_display(row.source_roll),
        print_date=print_date.isoformat(),
    )
