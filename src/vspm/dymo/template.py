"""Read, inspect and populate DYMO Label v8 `.label` (DieCutLabel XML) files.

The original files are only ever read. `populate()` returns a new XML string.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from lxml import etree

from vspm.domain.models import LabelContent, TemplateKey


class TemplateError(Exception):
    pass


@dataclass
class TemplateSpec:
    key: TemplateKey
    description: str
    files: list[str]
    barcode_prefix: str
    excel_category: str
    label_language: str
    thickness_decimal: str
    text_object: str
    text_runs: list[str]
    weight_object: str
    barcode_object: str
    counter_object: str


@dataclass
class TemplateRegistry:
    specs: dict[TemplateKey, TemplateSpec]
    template_dir: Path
    _cache: dict[TemplateKey, "LabelTemplate"] = field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: Path, template_dir: Path) -> "TemplateRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        specs: dict[TemplateKey, TemplateSpec] = {}
        for k, v in data["templates"].items():
            key = TemplateKey(k)
            specs[key] = TemplateSpec(key=key, **v)
        return cls(specs=specs, template_dir=Path(template_dir))

    def spec(self, key: TemplateKey) -> TemplateSpec:
        return self.specs[TemplateKey(key)]

    def find_file(self, key: TemplateKey) -> Path | None:
        for name in self.spec(key).files:
            p = self.template_dir / name
            if p.exists():
                return p
        return None

    def load(self, key: TemplateKey) -> "LabelTemplate":
        key = TemplateKey(key)
        if key not in self._cache:
            path = self.find_file(key)
            if path is None:
                raise TemplateError(
                    f"Template file for {key.value} not found in {self.template_dir} "
                    f"(expected one of {self.spec(key).files})"
                )
            tpl = LabelTemplate.load(path)
            tpl.check_spec(self.spec(key))
            self._cache[key] = tpl
        return self._cache[key]


def _local(tag: Any) -> str:
    return tag if isinstance(tag, str) else ""


class LabelTemplate:
    def __init__(self, path: Path | None, root: etree._Element):
        self.path = path
        self.root = root

    @classmethod
    def load(cls, path: Path) -> "LabelTemplate":
        return cls.from_bytes(Path(path).read_bytes(), path)

    @classmethod
    def from_bytes(cls, data: bytes, path: Path | None = None) -> "LabelTemplate":
        parser = etree.XMLParser(remove_blank_text=False, huge_tree=True)
        try:
            root = etree.fromstring(data, parser)
        except etree.XMLSyntaxError as e:
            raise TemplateError(f"Not a valid DYMO label XML: {e}") from e
        if root.tag != "DieCutLabel":
            raise TemplateError(f"Unsupported label root element <{root.tag}>")
        return cls(path, root)

    # ---- object access ---------------------------------------------------
    def objects(self) -> list[tuple[etree._Element, etree._Element]]:
        """[(object_element, bounds_element)] in document order."""
        out = []
        for oi in self.root.findall("ObjectInfo"):
            obj = next((c for c in oi if _local(c.tag) and c.tag != "Bounds"), None)
            out.append((obj, oi.find("Bounds")))
        return out

    def find(self, name: str, root: etree._Element | None = None) -> etree._Element | None:
        root = self.root if root is None else root
        for oi in root.findall("ObjectInfo"):
            for obj in oi:
                if obj.tag != "Bounds" and obj.findtext("Name") == name:
                    return obj
        return None

    @property
    def paper_name(self) -> str:
        return self.root.findtext("PaperName") or ""

    @property
    def orientation(self) -> str:
        return self.root.findtext("PaperOrientation") or ""

    def label_size_twips(self) -> tuple[float, float]:
        """(width, height) as laid out on screen (landscape swaps the paper)."""
        rr = self.root.find("DrawCommands/RoundRectangle")
        if rr is None:
            raise TemplateError("Label has no RoundRectangle draw command")
        w, h = float(rr.get("Width")), float(rr.get("Height"))
        if self.orientation == "Landscape":
            w, h = h, w
        return w, h

    def counter_info(self, counter_name: str = "COUNTER") -> dict[str, Any] | None:
        c = self.find(counter_name)
        if c is None or c.tag != "CounterObject":
            return None
        return {
            "prefix": c.findtext("PreText") or "",
            "postfix": c.findtext("PostText") or "",
            "start": int(c.findtext("Start") or 0),
            "current": int(c.findtext("Current") or 0),
            "increment": int(c.findtext("Increment") or 1),
            "format_width": int(c.findtext("FormatWidth") or 0),
            "leading_zeros": (c.findtext("UseLeadingZeros") or "").lower() == "true",
        }

    def text_runs(self, name: str) -> list[str]:
        obj = self.find(name)
        if obj is None:
            return []
        return [s.text or "" for s in obj.iter("String")]

    def check_spec(self, spec: TemplateSpec) -> None:
        problems = []
        text = self.find(spec.text_object)
        if text is None:
            problems.append(f"text object '{spec.text_object}' missing")
        else:
            n = len(text.findall("StyledText/Element"))
            if n != len(spec.text_runs):
                problems.append(
                    f"text object has {n} styled runs but templates.yaml defines "
                    f"{len(spec.text_runs)} - review the mapping"
                )
        for n in (spec.weight_object, spec.barcode_object):
            if self.find(n) is None:
                problems.append(f"object '{n}' missing")
        bc = self.find(spec.barcode_object)
        if bc is not None and bc.tag != "BarcodeObject":
            problems.append(f"'{spec.barcode_object}' is not a BarcodeObject")
        ci = self.counter_info(spec.counter_object)
        if ci is not None and ci["prefix"] != spec.barcode_prefix:
            problems.append(
                f"counter prefix in file is '{ci['prefix']}' but config says "
                f"'{spec.barcode_prefix}'"
            )
        if problems:
            raise TemplateError(f"{self.path}: " + "; ".join(problems))

    # ---- report ------------------------------------------------------------
    def inspect(self) -> dict[str, Any]:
        objs = []
        for obj, b in self.objects():
            if obj is None:
                continue
            d: dict[str, Any] = {
                "type": obj.tag,
                "name": obj.findtext("Name"),
                "bounds": {k: round(float(v), 1) for k, v in (b.attrib if b is not None else {}).items()},
                "is_variable": obj.findtext("IsVariable"),
                "linked_object": obj.findtext("LinkedObjectName") or "",
            }
            if obj.tag in ("TextObject", "AddressObject"):
                d["runs"] = []
                for el in obj.findall("StyledText/Element"):
                    f = el.find("Attributes/Font")
                    d["runs"].append(
                        {
                            "text": el.findtext("String"),
                            "font": f"{f.get('Family')} {f.get('Size')}"
                            + (" bold" if f.get("Bold") == "True" else ""),
                        }
                    )
                d["fit"] = obj.findtext("TextFitMode")
                d["align"] = f"{obj.findtext('HorizontalAlignment')}/{obj.findtext('VerticalAlignment')}"
            elif obj.tag == "BarcodeObject":
                d.update(
                    text=obj.findtext("Text"),
                    symbology=obj.findtext("Type"),
                    size=obj.findtext("Size"),
                    text_position=obj.findtext("TextPosition"),
                )
            elif obj.tag == "CounterObject":
                d.update(self.counter_info(obj.findtext("Name")) or {})
            elif obj.tag == "DateTimeObject":
                d.update(
                    format=obj.findtext("DateTimeFormat"),
                    include_time=obj.findtext("IncludeTime"),
                    use_24h=obj.findtext("Use24HourFormat"),
                    font=f"{obj.find('Font').get('Family')} {obj.find('Font').get('Size')}",
                )
            elif obj.tag == "ImageObject":
                d["image_base64_chars"] = len(obj.findtext("Image") or "")
                d["scale_mode"] = obj.findtext("ScaleMode")
            elif obj.tag == "ShapeObject":
                d["shape"] = obj.findtext("ShapeType")
            objs.append(d)
        w, h = self.label_size_twips()
        return {
            "file": self.path.name if self.path else None,
            "paper": self.paper_name,
            "orientation": self.orientation,
            "size_twips": [w, h],
            "size_mm": [round(w / 1440 * 25.4, 1), round(h / 1440 * 25.4, 1)],
            "objects": objs,
        }

    # ---- populate ------------------------------------------------------------
    def populate(self, spec: TemplateSpec, content: LabelContent, number: int) -> str:
        """Return a new label XML with the given content and barcode number."""
        self.check_spec(spec)
        root = copy.deepcopy(self.root)
        values = {
            "material": content.material,
            "thickness": content.thickness,
            "dimensions": content.dimensions,
            "pieces": content.pieces,
            "customer": content.customer_short,
            "weight": content.weight,
        }
        text = self.find(spec.text_object, root)
        elements = text.findall("StyledText/Element")
        for el, fmt in zip(elements, spec.text_runs):
            el.find("String").text = fmt.format(**values)

        q = self.find(spec.weight_object, root)
        strings = list(q.iter("String"))
        strings[0].text = content.weight
        for extra in strings[1:]:
            extra.text = ""

        bc = self.find(spec.barcode_object, root)
        bc.find("Text").text = content.barcode
        linked = bc.find("LinkedObjectName")
        if linked is not None:
            linked.text = None  # break link: DYMO must print exactly our number

        counter = self.find(spec.counter_object, root)
        if counter is not None and counter.tag == "CounterObject":
            # Keep the (tiny) counter object visually consistent with the barcode.
            counter.find("Current").text = str(number)
        xml = etree.tostring(root, encoding="utf-8", xml_declaration=True)
        return xml.decode("utf-8")


def format_barcode(prefix: str, number: int, format_width: int = 0, leading_zeros: bool = True) -> str:
    """Mimic DYMO's counter formatting: prefix + number padded to FormatWidth."""
    if number < 0:
        raise ValueError("barcode number must be positive")
    digits = str(number)
    if leading_zeros and format_width and len(digits) < format_width:
        digits = digits.zfill(format_width)
    return f"{prefix}{digits}"
