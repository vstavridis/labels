"""Write the printed barcodes onto the scanned production sheet.

The sheet is a printed table (ΕΙΔΟΣ … ΔΕΜΑΤΑ | BARCODE). The grid is found from its long
dark lines; the BARCODE column is the rightmost column of the main table and data row N is
the N-th row under the header. Each printed barcode is written into its row's BARCODE cell.
A phone photo is usually a little tilted: the table is straightened first (only to find it; the text
is turned back to the photo's angle). If the grid cannot be found with confidence, nothing is guessed:
a white strip listing the barcodes in row order is added below the photo instead.

Always drawn on the ORIGINAL scan (never on an already stamped one), so stamping again
with more barcodes gives the same result and no quality is lost.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps


@dataclass
class Grid:
    rows: list[tuple[int, int]]  # (top, bottom) of each data row, in order
    barcode_x: tuple[int, int]  # (left, right) of the BARCODE column
    angle: float = 0.0  # the photo was turned by this many degrees to straighten the table


def _dilate(mask: np.ndarray, k: int, axis: int) -> np.ndarray:
    """Thicken lines by k pixels along `axis` (a slightly tilted or wavy line still counts as one line)."""
    out = mask.copy()
    for d in range(1, k + 1):
        if axis == 0:
            out[d:, :] |= mask[:-d, :]
            out[:-d, :] |= mask[d:, :]
        else:
            out[:, d:] |= mask[:, :-d]
            out[:, :-d] |= mask[:, d:]
    return out


def skew_angle(dark: np.ndarray) -> float:
    """Angle (degrees) that makes the table's lines horizontal: the rotation with the sharpest row profile.
    Phone photos of the sheet are rarely perfectly straight."""
    h, w = dark.shape
    f = min(1.0, 800 / max(w, 1))
    small = Image.fromarray((dark * 255).astype(np.uint8)).resize((max(1, int(w * f)), max(1, int(h * f))))

    def score(a: float) -> float:
        r = np.asarray(small.rotate(a, resample=Image.BILINEAR, fillcolor=0), dtype=np.float32)
        prof = r.sum(axis=1)
        return float((prof * prof).sum())

    best = max(np.arange(-5.0, 5.01, 0.5), key=score)
    best = max(np.arange(best - 0.5, best + 0.51, 0.1), key=score)
    return float(round(best, 2))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Consecutive True runs as (start, end) inclusive."""
    out, start = [], None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(mask) - 1))
    return out


def _merge(pos: list[int], tol: int) -> list[int]:
    out: list[list[int]] = []
    for p in pos:
        if out and p - out[-1][-1] <= tol:
            out[-1].append(p)
        else:
            out.append([p])
    return [int(round(sum(g) / len(g))) for g in out]


def _vlines(dark: np.ndarray, y0: int, y1: int, frac: float = 0.8) -> list[int]:
    band = dark[y0 + 1:y1, :] if y1 - y0 > 2 else dark[y0:y1 + 1, :]
    if band.shape[0] == 0:
        return []
    return [(a + b) // 2 for a, b in _runs(band.mean(axis=0) > frac)]


def _has(lines: list[int], x: int, tol: int) -> bool:
    return any(abs(v - x) <= tol for v in lines)


def find_grid(img: Image.Image) -> Optional[Grid]:
    """Find the BARCODE column (the rightmost column whose two borders appear in most bands), take the
    consecutive bands that contain both borders as the table, merge slivers (thick header text, missing
    faint lines) into whole rows; the first row is the header."""
    gray = np.asarray(img.convert("L"), dtype=np.int16)
    paper = float(np.percentile(gray, 90))
    angle = skew_angle(gray < paper - 50)
    if abs(angle) >= 0.1:
        img = img.rotate(angle, resample=Image.BILINEAR, fillcolor="white")
        gray = np.asarray(img.convert("L"), dtype=np.int16)
    else:
        angle = 0.0
    grid = _find_grid_straight(gray, paper)
    if grid is not None:
        grid.angle = angle
    return grid


def _find_grid_straight(gray: np.ndarray, paper: float) -> Optional[Grid]:
    h, w = gray.shape
    dark = gray < paper - 50  # printed lines, relative to this scan's paper colour
    k = max(1, int(round(min(h, w) * 0.002)))  # what is left of a tilt / bend after straightening
    hl = _merge([(a + b) // 2 for a, b in _runs(_dilate(dark, k, 0).mean(axis=1) > 0.5)],
                max(1, int(h * 0.004)) + k)
    dark = _dilate(dark, k, 1)
    bands = [(hl[i], hl[i + 1]) for i in range(len(hl) - 1) if hl[i + 1] - hl[i] >= 2]
    if len(bands) < 3:
        return None
    tol = max(3, int(w * 0.015))
    structs = [_vlines(dark, a, b) for a, b in bands]
    votes: dict[tuple[int, int], int] = {}
    for st in structs:
        if len(st) >= 4 and 0.04 * w <= st[-1] - st[-2] <= 0.45 * w:
            key = (st[-2] // tol, st[-1] // tol)
            votes[key] = votes.get(key, 0) + 1
    if not votes:
        return None
    key = max(votes, key=votes.get)
    pair = next(st[-2:] for st in structs if len(st) >= 4 and (st[-2] // tol, st[-1] // tol) == key)
    left_x, right_x = pair
    inside = [_has(st, left_x, tol) and _has(st, right_x, tol) for st in structs]
    full = [b - a for (a, b), ok in zip(bands, inside) if ok and b - a >= max(4, h * 0.015)]
    if not full:
        return None
    unit = sorted(full)[len(full) // 2]
    # the longest run of bands inside the table (slivers between them count as inside)
    best: tuple[int, int] = (0, -1)
    i = 0
    while i < len(bands):
        if inside[i] or bands[i][1] - bands[i][0] < 0.6 * unit:
            j = i
            while j + 1 < len(bands) and (inside[j + 1] or bands[j + 1][1] - bands[j + 1][0] < 0.6 * unit):
                j += 1
            if j - i > best[1] - best[0]:
                best = (i, j)
            i = j + 1
        else:
            i += 1
    groups: list[list[int]] = []
    for a, b in bands[best[0]:best[1] + 1]:
        if groups and (groups[-1][1] - groups[-1][0] < 0.6 * unit or b - a < 0.6 * unit):
            groups[-1][1] = b  # merge slivers into a whole row
        else:
            groups.append([a, b])
    if groups and groups[-1][1] - groups[-1][0] < 0.6 * unit:
        groups.pop()
    if len(groups) < 2:  # header + at least one data row
        return None
    # The photo may be cut right at the table's right border (that line is then missing): if the
    # table's horizontal lines go on to the right of the column found, that is one more column - the
    # real BARCODE column - ending at the edge of the photo.
    cw = right_x - left_x
    if w - 1 - right_x >= 0.6 * cw:
        x_end = min(w - 1, right_x + int(1.3 * cw))
        ys = [y for y in hl if groups[0][0] - 2 <= y <= groups[-1][1] + 2]
        seg = dark[:, right_x + max(3, cw // 20):max(right_x + max(3, cw // 20) + 1, x_end - max(3, cw // 20))]
        cont = [seg[max(0, y - k):y + k + 1, :].any(axis=0).mean() > 0.6 for y in ys]
        if ys and sum(cont) >= 0.7 * len(ys):
            # its right border, if it is in the photo: a vertical line found in most rows of the table
            table = [st for st, ok in zip(structs, inside) if ok]
            vs = sorted({x for st in table for x in st if x > right_x + cw // 2})
            vs = [x for x in vs if sum(_has(st, x, tol) for st in table) >= 0.5 * len(table)]
            left_x, right_x = right_x, (vs[0] if vs else w - 1)
    rows: list[tuple[int, int]] = []
    for a, b in groups[1:]:
        k = max(1, int(round((b - a) / unit)))  # a faint missing line: split the double row evenly
        for n in range(k):
            rows.append((a + (b - a) * n // k, a + (b - a) * (n + 1) // k))
    return Grid(rows=rows, barcode_x=(left_x, right_x))


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def _fit_text(draw: ImageDraw.ImageDraw, text: str, box_w: int, box_h: int):
    size = max(8, int(box_h * 0.7))
    while size > 8:
        f = _font(size)
        l, t, r, b = draw.textbbox((0, 0), text, font=f)
        if r - l <= box_w * 0.94 and b - t <= box_h * 0.8:
            return f, (r - l, b - t, l, t)
        size -= 1
    f = _font(8)
    l, t, r, b = draw.textbbox((0, 0), text, font=f)
    return f, (r - l, b - t, l, t)


def stamp(original: bytes, entries: list[tuple[int, str]], quality: int = 92) -> tuple[bytes, str]:
    """Return (JPEG bytes, mode) with the barcodes written in. entries = [(row number 1-based, barcode)].
    mode = 'cells' (written into the BARCODE cells) or 'strip' (list added below the photo)."""
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(original))).convert("RGB")
    grid = find_grid(img)
    ink = (200, 0, 0)
    if grid is not None and all(1 <= n <= len(grid.rows) for n, _ in entries):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))  # straightened coordinates
        draw = ImageDraw.Draw(layer)
        x0, x1 = grid.barcode_x
        for n, code in entries:
            y0, y1 = grid.rows[n - 1]
            pad = max(2, (x1 - x0) // 30)
            cw, ch = x1 - x0 - 2 * pad, y1 - y0 - 2 * pad
            f, (tw, th, ox, oy) = _fit_text(draw, code, cw, ch)
            draw.text((x0 + pad + (cw - tw) / 2 - ox, y0 + pad + (ch - th) / 2 - oy), code, fill=ink + (255,),
                      font=f)
        if grid.angle:
            layer = layer.rotate(-grid.angle, resample=Image.BICUBIC)  # back onto the photo as it is
        img = Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB")
        mode = "cells"
    else:
        line_h = max(24, img.height // 30)
        f = _font(int(line_h * 0.7))
        strip = Image.new("RGB", (img.width, line_h * (len(entries) + 1)), "white")
        d = ImageDraw.Draw(strip)
        d.text((10, 4), "BARCODE", fill=(0, 0, 0), font=f)
        for i, (n, code) in enumerate(entries, 1):
            d.text((10, 4 + i * line_h), code, fill=ink, font=f)
        out = Image.new("RGB", (img.width, img.height + strip.height), "white")
        out.paste(img, (0, 0))
        out.paste(strip, (0, img.height))
        img, mode = out, "strip"
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), mode
