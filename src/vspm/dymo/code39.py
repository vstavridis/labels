"""Code 39 encoder (used only for on-screen previews; DYMO renders the real barcode)."""

from __future__ import annotations

# 9 elements per character, alternating bar/space starting with a bar; 1 = wide.
PATTERNS = {
    "0": "000110100", "1": "100100001", "2": "001100001", "3": "101100000",
    "4": "000110001", "5": "100110000", "6": "001110000", "7": "000100101",
    "8": "100100100", "9": "001100100", "A": "100001001", "B": "001001001",
    "C": "101001000", "D": "000011001", "E": "100011000", "F": "001011000",
    "G": "000001101", "H": "100001100", "I": "001001100", "J": "000011100",
    "K": "100000011", "L": "001000011", "M": "101000010", "N": "000010011",
    "O": "100010010", "P": "001010010", "Q": "000000111", "R": "100000110",
    "S": "001000110", "T": "000010110", "U": "110000001", "V": "011000001",
    "W": "111000000", "X": "010010001", "Y": "110010000", "Z": "011010000",
    "-": "010000101", ".": "110000100", " ": "011000100", "*": "010010100",
    "$": "010101000", "/": "010100010", "+": "010001010", "%": "000101010",
}


def is_encodable(text: str) -> bool:
    return bool(text) and all(c in PATTERNS and c != "*" for c in text)


def modules(text: str, wide: int = 3) -> list[tuple[bool, int]]:
    """Return [(is_bar, width_in_narrow_units)] including start/stop '*'."""
    if not is_encodable(text):
        raise ValueError(f"Cannot encode {text!r} in Code 39")
    out: list[tuple[bool, int]] = []
    for i, ch in enumerate(f"*{text}*"):
        if i:
            out.append((False, 1))  # inter-character gap
        for j, w in enumerate(PATTERNS[ch]):
            out.append((j % 2 == 0, wide if w == "1" else 1))
    return out
