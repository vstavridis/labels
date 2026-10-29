"""Approximate PNG preview of a DYMO label XML using Pillow.

This reproduces the template's layout (object bounds, fonts, bold runs, lines,
images and a Code 39 barcode). It is a faithful *approximation*: DYMO's own
renderer (available through the DYMO web service, see printers.py) is used
for the exact preview when it is reachable.
"""

from __future__ import annotations

import base64
import io
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from lxml import etree
from PIL import Image, ImageDraw, ImageFont

from vspm.dymo import code39

GREEK_MONTHS = [
    "Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου",
    "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου",
]

FONT_CANDIDATES = {
    ("arial", False): ["C:/Windows/Fonts/arial.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
                       "/Library/Fonts/Arial.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans.ttf"],
    ("arial", True): ["C:/Windows/Fonts/arialbd.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
                      "/Library/Fonts/Arial Bold.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"],
    ("impact", False): ["C:/Windows/Fonts/impact.ttf", "/System/Library/Fonts/Supplemental/Impact.ttf",
                        "/Library/Fonts/Impact.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                        "DejaVuSans-Bold.ttf"],
}


@lru_cache(maxsize=256)
def _font(family: str, bold: bool, px: int) -> ImageFont.ImageFont:
    fam = "impact" if family.lower() == "impact" else "arial"
    key = (fam, bold if fam == "arial" else False)
    for cand in FONT_CANDIDATES[key]:
        try:
            return ImageFont.truetype(cand, max(px, 4))
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=max(px, 4))
    except TypeError:  # very old Pillow
        return ImageFont.load_default()


def format_dymo_datetime(dt: datetime, fmt: str, include_time: bool, h24: bool, lang: str = "el") -> str:
    if fmt == "DayMonthLongYear":
        month = GREEK_MONTHS[dt.month - 1] if lang == "el" else dt.strftime("%B")
        s = f"{dt.day} {month} {dt.year}"
    else:
        s = dt.strftime("%d/%m/%Y")
    if include_time:
        s += " " + (dt.strftime("%H:%M") if h24 else dt.strftime("%I:%M %p"))
    return s


class _Ctx:
    def __init__(self, scale: float, img: Image.Image):
        self.s = scale
        self.img = img
        self.d = ImageDraw.Draw(img)

    def box(self, b) -> tuple[int, int, int, int]:
        x, y, w, h = (float(b.get(k)) for k in ("X", "Y", "Width", "Height"))
        return (round(x * self.s), round(y * self.s), round((x + w) * self.s), round((y + h) * self.s))


def _layout_runs(ctx: _Ctx, runs, box, valign: str, halign: str, shrink: bool):
    """runs: [(text, family, size_pt, bold)] -> draws text inside box."""
    x0, y0, x1, y1 = box
    bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)

    def build(factor: float):
        lines: list[list[tuple[str, ImageFont.ImageFont]]] = [[]]
        for text, fam, size, bold in runs:
            px = int(size * 20 * ctx.s * factor)
            f = _font(fam, bold, px)
            parts = text.split("\n")
            for i, part in enumerate(parts):
                if i:
                    lines.append([])
                if part:
                    lines[-1].append((part, f))
        metrics = []
        for ln in lines:
            w = sum(ctx.d.textlength(t, font=f) for t, f in ln)
            h = max((f.size for _, f in ln), default=int(runs[0][2] * 20 * ctx.s * factor) if runs else 10)
            metrics.append((w, h * 1.15))
        return lines, metrics

    factor = 1.0
    lines, metrics = build(factor)
    if shrink:
        for _ in range(30):
            tw = max((m[0] for m in metrics), default=0)
            th = sum(m[1] for m in metrics)
            if tw <= bw and th <= bh:
                break
            factor *= 0.92
            lines, metrics = build(factor)
    th = sum(m[1] for m in metrics)
    y = y0 if valign == "Top" else (y1 - th if valign == "Bottom" else y0 + (bh - th) / 2)
    for ln, (w, h) in zip(lines, metrics):
        x = x0 if halign == "Left" else (x1 - w if halign == "Right" else x0 + (bw - w) / 2)
        for t, f in ln:
            ctx.d.text((x, y), t, font=f, fill="black")
            x += ctx.d.textlength(t, font=f)
        y += h


def _styled_runs(obj):
    runs = []
    for el in obj.findall("StyledText/Element"):
        f = el.find("Attributes/Font")
        runs.append((el.findtext("String") or "", f.get("Family"), float(f.get("Size")), f.get("Bold") == "True"))
    return runs


def render_label_png(xml: str | bytes, now: datetime | None = None, scale: float = 0.25, lang: str = "el") -> bytes:
    """Render label XML to PNG bytes. scale = pixels per twip (0.25 ≈ 360 dpi)."""
    root = etree.fromstring(xml.encode("utf-8") if isinstance(xml, str) else xml,
                            etree.XMLParser(huge_tree=True))
    rr = root.find("DrawCommands/RoundRectangle")
    w, h = float(rr.get("Width")), float(rr.get("Height"))
    if (root.findtext("PaperOrientation") or "") == "Landscape":
        w, h = h, w
    img = Image.new("RGB", (round(w * scale), round(h * scale)), "white")
    ctx = _Ctx(scale, img)
    ctx.d.rounded_rectangle((0, 0, img.width - 1, img.height - 1), radius=int(270 * scale), outline="#bbbbbb", width=2)
    now = now or datetime.now()

    for oi in root.findall("ObjectInfo"):
        b = oi.find("Bounds")
        obj = next(c for c in oi if c.tag != "Bounds")
        box = ctx.box(b)
        tag = obj.tag
        if tag == "ShapeObject":
            lw = max(1, round(float(obj.findtext("LineWidth") or 15) * scale))
            st = obj.findtext("ShapeType")
            x0, y0, x1, y1 = box
            if st == "HorizontalLine":
                ym = (y0 + y1) // 2
                ctx.d.line((x0, ym, x1, ym), fill="black", width=lw)
            elif st == "VerticalLine":
                xm = (x0 + x1) // 2
                ctx.d.line((xm, y0, xm, y1), fill="black", width=lw)
            else:
                ctx.d.rectangle(box, outline="black", width=lw)
        elif tag in ("TextObject", "AddressObject"):
            _layout_runs(ctx, _styled_runs(obj), box, obj.findtext("VerticalAlignment") or "Middle",
                         obj.findtext("HorizontalAlignment") or "Left",
                         (obj.findtext("TextFitMode") or "") in ("ShrinkToFit", "AlwaysFit") or tag == "AddressObject")
        elif tag == "ImageObject":
            data = obj.findtext("Image")
            if not data:
                continue
            try:
                im = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGBA")
            except Exception:
                continue
            x0, y0, x1, y1 = box
            bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)
            if (obj.findtext("ScaleMode") or "None") == "None":
                # Natural size at 96 dpi -> twips (15 twips per pixel).
                tw, th = im.width * 15 * scale, im.height * 15 * scale
                r = min(1.0, bw / tw, bh / th)
            else:
                r = min(bw / im.width, bh / im.height)
                tw, th = im.width, im.height
            nw, nh = max(1, int((tw if (obj.findtext("ScaleMode") or "None") == "None" else im.width) * r)), max(
                1, int((th if (obj.findtext("ScaleMode") or "None") == "None" else im.height) * r))
            im = im.resize((nw, nh))
            img.paste(im, (x0 + (bw - nw) // 2, y0 + (bh - nh) // 2), im)
        elif tag == "BarcodeObject":
            text = obj.findtext("Text") or ""
            x0, y0, x1, y1 = box
            text_h = int((y1 - y0) * 0.22) if obj.findtext("TextPosition") == "Bottom" else 0
            if code39.is_encodable(text):
                mods = code39.modules(text)
                total = sum(wd for _, wd in mods)
                unit = (x1 - x0) / total * 0.95
                x = x0 + ((x1 - x0) - unit * total) / 2
                for bar, wd in mods:
                    if bar:
                        ctx.d.rectangle((x, y0, x + unit * wd - 0.5, y1 - text_h), fill="black")
                    x += unit * wd
            else:
                ctx.d.rectangle(box, outline="red", width=2)
            if text_h:
                tf = obj.find("TextFont")
                f = _font(tf.get("Family"), tf.get("Bold") == "True", max(text_h - 2, 6))
                tw = ctx.d.textlength(text, font=f)
                ctx.d.text((x0 + ((x1 - x0) - tw) / 2, y1 - text_h), text, font=f, fill="black")
        elif tag == "DateTimeObject":
            f = obj.find("Font")
            s = format_dymo_datetime(now, obj.findtext("DateTimeFormat") or "", obj.findtext("IncludeTime") == "True",
                                     obj.findtext("Use24HourFormat") == "True", lang)
            s = (obj.findtext("PreText") or "") + s + (obj.findtext("PostText") or "")
            _layout_runs(ctx, [(s, f.get("Family"), float(f.get("Size")), f.get("Bold") == "True")], box,
                         obj.findtext("VerticalAlignment") or "Top", obj.findtext("HorizontalAlignment") or "Left", True)
        elif tag == "CounterObject":
            # Tiny object hidden under the barcode in the supplied templates; skip.
            continue

    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def save_preview(xml: str, path: Path, now: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_label_png(xml, now=now))
    return path
