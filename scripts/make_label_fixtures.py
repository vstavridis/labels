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
    """Writes VS.label, PG.label, PG0.label, EX.label, recognised by COUNTER prefix."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from vspm.dymo.template import counter_prefix_of

    dst.mkdir(parents=True, exist_ok=True)
    for p in sorted(src.glob("*.label")):
        prefix = counter_prefix_of(p)
        if prefix in ("VS", "PG", "PG0", "EX"):
            (dst / f"{prefix}.label").write_bytes(sanitize(p.read_bytes()))
            print(f"{prefix} template -> {dst / (prefix + '.label')}")


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
