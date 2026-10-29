"""Create sanitized DYMO label fixtures for automated tests.

Keeps the structure (object names, bounds, fonts, run counts, counters) of the
real templates but removes embedded logos and replaces customer / company text,
so the fixtures can be committed. Usage:

    python scripts/make_label_fixtures.py reference_files tests/fixtures/labels
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from lxml import etree

TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")

NAMES = {
    "ΕΤΙΚΕΤΑ ΚΟΜΜΕΝΑ-1.label": "VS.label",
    "ΕΤΙΚΕΤΑ ΚΟΜΜΕΝΑ-1(1).label": "VS.label",
    "ΕΤΙΚΕΤΑ ΠΑΓΟΥΝΗ-1.label": "PG.label",
    "ΕΤΙΚΕΤΑ ΠΑΓΟΥΝΗ-2.label": "PG0.label",
    "ΕΤΙΚΕΤΑ ΕΞΑΓΩΓΕΣ.label": "EX.label",
}


def sanitize(data: bytes) -> bytes:
    root = etree.fromstring(data, etree.XMLParser(huge_tree=True))
    for img in root.iter("Image"):
        img.text = TINY_PNG
    for obj in root.iter("AddressObject"):
        for s in obj.iter("String"):
            s.text = "TEST COMPANY"
    for s in root.iter("String"):
        if s.text:
            s.text = re.sub(r"((?:ΠΕΛΑΤΗΣ|CUSTOMER)\s*:\s*).+", r"\1TEST CUSTOMER", s.text)
            s.text = s.text.replace("VSTAVRIDIS.GR", "EXAMPLE.COM")
    return etree.tostring(root, xml_declaration=True, encoding="utf-8")


def main(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for name, out in NAMES.items():
        p = src / name
        if p.exists():
            (dst / out).write_bytes(sanitize(p.read_bytes()))
            print(f"{name} -> {dst / out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
