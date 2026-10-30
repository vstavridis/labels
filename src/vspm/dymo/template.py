"""Read, inspect and populate DYMO Label v8 `.label` (DieCutLabel XML) files.

The original files are only ever read. `populate()` returns a new XML string.
"""

from __future__ import annotations

import copy
import hashlib
import re
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
    barcode_prefix: str
    excel_category: str
    label_language: str
    thickness_decimal: str
    text_object: str
    text_runs: list[str]
    weight_object: str
    barcode_object: str
    counter_object: str
    files: list[str] = field(default_factory=list)


def parse_template_files(value: str) -> dict[str, str]:
    """`VS=a.label;PG=b.label` -> {"VS": "a.label", "PG": "b.label"}."""
    out: dict[str, str] = {}
    for part in filter(None, (p.strip() for p in (value or "").split(";"))):
        if "=" not in part:
            raise ValueError(f"Invalid VSPM_TEMPLATE_FILES entry '{part}' (expected KEY=file.label)")
        k, v = part.split("=", 1)
        out[TemplateKey(k.strip()).value] = v.strip()
    return out


class TemplateStore:
    """Templates uploaded through the app, stored byte-exact in the database (all versions kept)."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def content(row) -> bytes:
        import base64
        return base64.b64decode(row["content_b64"])

    def active(self, key: TemplateKey):
        return self.db.one("SELECT * FROM label_templates WHERE template_key=? AND active=1 ORDER BY id DESC LIMIT 1",
                           (TemplateKey(key).value,))

    def by_sha(self, key: TemplateKey, sha256: str) -> bytes | None:
        row = self.db.one("SELECT * FROM label_templates WHERE template_key=? AND sha256=?",
                          (TemplateKey(key).value, sha256))
        return self.content(row) if row else None

    def versions(self, key: TemplateKey | None = None) -> list:
        sql = ("SELECT id, template_key, sha256, filename, counter_prefix, active, uploaded_by, uploaded_at "
               "FROM label_templates")
        if key is None:
            return self.db.query(sql + " ORDER BY template_key, id DESC")
        return self.db.query(sql + " WHERE template_key=? ORDER BY id DESC", (TemplateKey(key).value,))

    def upload(self, key: TemplateKey, spec: "TemplateSpec", filename: str, data: bytes, user: str) -> str:
        """Validate and activate a template. Returns its SHA-256. Raises TemplateError if unusable."""
        import base64
        from vspm.db.database import utcnow

        key = TemplateKey(key)
        tpl = LabelTemplate.from_bytes(data)
        tpl.check_spec(spec)
        info = tpl.counter_info(spec.counter_object)
        if info is not None and info["prefix"] != spec.barcode_prefix:
            raise TemplateError(f"{filename}: counter prefix {info['prefix']} - expected {spec.barcode_prefix}")
        problems = check_prepared_label(tpl.populate(spec, _sample_content(spec), 1), _sample_content(spec).barcode)
        if problems:
            raise TemplateError(f"{filename}: " + "; ".join(problems))
        with self.db.transaction() as conn:
            conn.execute("UPDATE label_templates SET active=0 WHERE template_key=?", (key.value,))
            existing = conn.execute("SELECT id FROM label_templates WHERE template_key=? AND sha256=?",
                                    (key.value, tpl.sha256)).fetchone()
            if existing:
                conn.execute("UPDATE label_templates SET active=1 WHERE id=?", (existing["id"],))
            else:
                conn.execute("INSERT INTO label_templates (template_key, sha256, filename, content_b64, counter_prefix, "
                             "active, uploaded_by, uploaded_at) VALUES (?,?,?,?,?,1,?,?)",
                             (key.value, tpl.sha256, filename, base64.b64encode(data).decode("ascii"),
                              info["prefix"] if info else None, user, utcnow()))
            self.db.audit("template", key.value, "template_uploaded", {"file": filename, "sha256": tpl.sha256},
                          user, conn=conn)
        return tpl.sha256


def _sample_content(spec: "TemplateSpec") -> LabelContent:
    return LabelContent(template_key=spec.key, material="ΤΕΣΤ", thickness="0,50", dimensions="1000", pieces="1", customer_short="TEST",
                        weight="1", barcode=f"{spec.barcode_prefix}1")


def counter_prefix_of(path: Path, counter_name: str = "COUNTER") -> str | None:
    """Counter PreText of a .label file (None if unreadable / no counter)."""
    try:
        tpl = LabelTemplate.load(path)
    except Exception:
        return None
    info = tpl.counter_info(counter_name)
    return info["prefix"] if info else None


@dataclass
class TemplateRegistry:
    """Finds the DYMO template for each label type.

    Resolution order: explicit file from VSPM_TEMPLATE_FILES, then names listed
    in templates.yaml, then automatic detection by the COUNTER prefix stored in
    each .label file (VS / PG / PG0 / EX). Detection never guesses: if two
    files share a prefix the operator must choose one in VSPM_TEMPLATE_FILES.
    """

    specs: dict[TemplateKey, TemplateSpec]
    template_dir: Path
    overrides: dict[str, str] = field(default_factory=dict)
    store: Any = None  # TemplateStore: uploaded templates, used before files when present
    _cache: dict[TemplateKey, "LabelTemplate"] = field(default_factory=dict)
    _prefixes: dict[Path, str | None] | None = None

    @classmethod
    def from_yaml(cls, path: Path, template_dir: Path, overrides: dict[str, str] | None = None) -> "TemplateRegistry":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        specs: dict[TemplateKey, TemplateSpec] = {}
        for k, v in data["templates"].items():
            key = TemplateKey(k)
            specs[key] = TemplateSpec(key=key, **v)
        return cls(specs=specs, template_dir=Path(template_dir), overrides=dict(overrides or {}))

    def spec(self, key: TemplateKey) -> TemplateSpec:
        return self.specs[TemplateKey(key)]

    def detected(self) -> dict[Path, str | None]:
        if self._prefixes is None:
            self._prefixes = {}
            if self.template_dir.exists():
                for p in sorted(self.template_dir.glob("*.label")):
                    self._prefixes[p] = counter_prefix_of(p)
        return self._prefixes

    def find_file(self, key: TemplateKey) -> Path | None:
        key = TemplateKey(key)
        spec = self.spec(key)
        if key.value in self.overrides:
            p = Path(self.overrides[key.value])
            p = p if p.is_absolute() else self.template_dir / p
            if not p.exists():
                raise TemplateError(f"VSPM_TEMPLATE_FILES: file for {key.value} not found: {p}")
            return p
        for name in spec.files:
            p = self.template_dir / name
            if p.exists():
                return p
        candidates = [p for p, prefix in self.detected().items() if prefix == spec.barcode_prefix]
        if len(candidates) > 1:
            raise TemplateError(
                f"Several templates in {self.template_dir} have counter prefix {spec.barcode_prefix}: "
                f"{[c.name for c in candidates]}. Choose one with VSPM_TEMPLATE_FILES={key.value}=<file name>.")
        return candidates[0] if candidates else None

    def source(self, key: TemplateKey) -> str:
        """Where the template for `key` comes from (for display)."""
        key = TemplateKey(key)
        if self.store is not None:
            row = self.store.active(key)
            if row is not None:
                return f"uploaded: {row['filename']} ({row['sha256'][:12]}…, {row['uploaded_at'][:16]})"
        path = self.find_file(key)
        return str(path) if path else "missing"

    def load_version(self, key: TemplateKey, sha256: str | None) -> "LabelTemplate | None":
        """The exact template version with this fingerprint (uploaded history or current file)."""
        key = TemplateKey(key)
        if not sha256:
            return None
        if self.store is not None:
            data = self.store.by_sha(key, sha256)
            if data is not None:
                return LabelTemplate.from_bytes(data)
        try:
            tpl = self.load(key)
        except TemplateError:
            return None
        return tpl if tpl.sha256 == sha256 else None

    def load(self, key: TemplateKey) -> "LabelTemplate":
        key = TemplateKey(key)
        if self.store is not None:
            row = self.store.active(key)
            if row is not None:
                cached = self._cache.get(key)
                if cached is None or cached.sha256 != row["sha256"]:
                    tpl = LabelTemplate.from_bytes(self.store.content(row))
                    tpl.check_spec(self.spec(key))
                    self._cache[key] = tpl
                return self._cache[key]
        if key not in self._cache or self._cache[key].path is None:
            path = self.find_file(key)
            if path is None:
                raise TemplateError(
                    f"No template for {key.value} in {self.template_dir}: no .label file whose COUNTER prefix is "
                    f"'{self.spec(key).barcode_prefix}' (upload it in Settings - DYMO templates, or set VSPM_TEMPLATE_FILES)."
                )
            tpl = LabelTemplate.load(path)
            tpl.check_spec(self.spec(key))
            self._cache[key] = tpl
        return self._cache[key]


def _local(tag: Any) -> str:
    return tag if isinstance(tag, str) else ""


class LabelTemplate:
    def __init__(self, path: Path | None, root: etree._Element, sha256: str | None = None):
        self.path = path
        self.root = root
        self.sha256 = sha256  # fingerprint of the original file (proves it was never modified)

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
        return cls(path, root, hashlib.sha256(data).hexdigest())

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
    def populate(self, spec: TemplateSpec, content: LabelContent, number: int, counter_mode: str = "sync") -> str:
        """Return a new label XML with the given content and barcode number.

        counter_mode "sync": the template's COUNTER object stays but prints the same
        text as the barcode; "remove": the COUNTER object is removed."""
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
            if counter_mode == "remove":
                oi = counter.getparent()
                oi.getparent().remove(oi)
            else:
                sync_counter_text(counter, content.barcode, number)
        xml = etree.tostring(root, encoding="utf-8", xml_declaration=True).decode("utf-8")
        problems = check_prepared_label(xml, content.barcode)
        if problems:  # never hand out a label that could print another number
            raise TemplateError("prepared label failed the safety check: " + "; ".join(problems))
        return xml


def counter_text(counter: etree._Element) -> str:
    """Text a DYMO CounterObject prints: PreText + Current (zero-padded) + PostText."""
    cur = (counter.findtext("Current") or "").strip()
    width = int(counter.findtext("FormatWidth") or 0)
    zeros = (counter.findtext("UseLeadingZeros") or "").strip().lower() == "true"
    digits = cur.zfill(width) if zeros and width > len(cur) else cur
    return (counter.findtext("PreText") or "") + digits + (counter.findtext("PostText") or "")


def _set_child(el: etree._Element, tag: str, text: str) -> None:
    c = el.find(tag)
    if c is None:
        c = etree.SubElement(el, tag)
    c.text = text


def sync_counter_text(counter: etree._Element, barcode: str, number: int) -> None:
    """Make the (small) COUNTER object print exactly the barcode text.

    For production numbers only `Current` changes (prefix/width stay as in the
    template). For other texts such as TEST0001 the prefix and width are set too.
    """
    _set_child(counter, "Current", str(number))
    if counter_text(counter) == barcode:
        return
    m = re.fullmatch(r"(.*?)(\d+)", barcode)
    if m:
        _set_child(counter, "PreText", m.group(1))
        _set_child(counter, "Current", str(int(m.group(2))))
        _set_child(counter, "FormatWidth", str(len(m.group(2))))
        _set_child(counter, "UseLeadingZeros", "True")
        post = counter.find("PostText")
        if post is not None:
            post.text = None
    if counter_text(counter) != barcode:
        raise TemplateError(f"cannot make the DYMO counter object print '{barcode}'")


def check_prepared_label(xml: str, barcode: str) -> list[str]:
    """Safety check of a prepared label (same rules as the browser bridge).

    The label must print exactly `barcode`: every barcode object carries it, no
    object is linked to a DYMO COUNTER (DYMO would then substitute its own
    number), and any remaining counter object prints the same text."""
    problems: list[str] = []
    if not re.fullmatch(r"[0-9A-Z\-. $/+%]+", barcode or ""):
        problems.append("barcode is not valid Code 39 text")
    try:
        root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml, etree.XMLParser(huge_tree=True))
    except etree.XMLSyntaxError as e:
        return [f"label XML cannot be read: {e}"]
    if root.tag != "DieCutLabel":
        problems.append("not a DYMO Label v8 (DieCutLabel) file")
    counters = list(root.iter("CounterObject"))
    counter_names = {c.findtext("Name") for c in counters}
    barcodes = list(root.iter("BarcodeObject"))
    if not barcodes:
        problems.append("label has no barcode object")
    for b in barcodes:
        if (b.findtext("Text") or "") != barcode:
            problems.append(f"barcode object prints '{b.findtext('Text')}' instead of '{barcode}'")
        if (b.findtext("LinkedObjectName") or "").strip():
            problems.append("barcode is linked to another object (DYMO counter)")
    for link in root.iter("LinkedObjectName"):
        if (link.text or "").strip() in counter_names:
            problems.append(f"an object is linked to the DYMO counter '{link.text.strip()}'")
    for c in counters:
        if counter_text(c) != barcode:
            problems.append(f"counter object would print '{counter_text(c)}' instead of '{barcode}'")
    return problems


def xml_sha256(xml: str) -> str:
    return hashlib.sha256(xml.encode("utf-8")).hexdigest()


def format_barcode(prefix: str, number: int, format_width: int = 0, leading_zeros: bool = True) -> str:
    """Mimic DYMO's counter formatting: prefix + number padded to FormatWidth."""
    if number < 0:
        raise ValueError("barcode number must be positive")
    digits = str(number)
    if leading_zeros and format_width and len(digits) < format_width:
        digits = digits.zfill(format_width)
    return f"{prefix}{digits}"
