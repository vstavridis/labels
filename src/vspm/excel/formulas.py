"""Row translation for Excel formulas that keeps external-workbook links intact.

openpyxl's Translator rewrites `[1]!Table4[ΠΟΙΟΤΗΤΑ]` to `Table4[ΠΟΙΟΤΗΤΑ]`,
silently dropping the external-workbook reference. This translator only
touches A1-style references outside string literals, quoted sheet names and
square brackets ([1] external-book indices, structured table references).
"""

from __future__ import annotations

import re

# Cell reference such as K329, $K329, K$329, $K$329 (columns up to XFD).
_CELL = re.compile(
    r"(?<![A-Za-z0-9_.$Ͱ-Ͽἀ-῿])"
    r"(\$?)([A-Z]{1,3})(\$?)([0-9]{1,7})"
    r"(?![A-Za-z0-9_(!Ͱ-Ͽ])"
)
# Row-only range such as 3:5 or $3:$5.
_ROWS = re.compile(r"(?<![A-Za-z0-9_.$:Ͱ-Ͽ])(\$?)([0-9]{1,7}):(\$?)([0-9]{1,7})(?![0-9A-Za-z_:])")

MAX_ROW = 1_048_576


def _segments(formula: str) -> list[tuple[bool, str]]:
    """Split into (translatable, text) segments."""
    out: list[tuple[bool, str]] = []
    buf = ""
    i, n = 0, len(formula)
    while i < n:
        ch = formula[i]
        if ch in "\"'":
            if buf:
                out.append((True, buf)); buf = ""
            j = i + 1
            while j < n:
                if formula[j] == ch:
                    if j + 1 < n and formula[j + 1] == ch:  # escaped quote
                        j += 2
                        continue
                    break
                j += 1
            out.append((False, formula[i:j + 1]))
            i = j + 1
        elif ch == "[":
            if buf:
                out.append((True, buf)); buf = ""
            depth, j = 0, i
            while j < n:
                if formula[j] == "[":
                    depth += 1
                elif formula[j] == "]":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append((False, formula[i:j + 1]))
            i = j + 1
        else:
            buf += ch
            i += 1
    if buf:
        out.append((True, buf))
    return out


def shift_rows(formula: str, delta: int) -> str:
    """Shift every relative row reference by `delta`; absolute rows ($5) stay."""

    def cell(m: re.Match) -> str:
        cabs, col, rabs, row = m.groups()
        r = int(row)
        if not rabs:
            r += delta
            if not 1 <= r <= MAX_ROW:
                raise ValueError(f"Row reference out of range after shifting {m.group(0)} by {delta}")
        return f"{cabs}{col}{rabs}{r}"

    def rows(m: re.Match) -> str:
        a1, r1, a2, r2 = m.groups()
        n1 = int(r1) + (0 if a1 else delta)
        n2 = int(r2) + (0 if a2 else delta)
        return f"{a1}{n1}:{a2}{n2}"

    parts = []
    for translatable, text in _segments(formula):
        if translatable:
            text = _ROWS.sub(rows, _CELL.sub(cell, text))
        parts.append(text)
    return "".join(parts)


def translate_to_row(formula: str, from_row: int, to_row: int) -> str:
    return shift_rows(formula, to_row - from_row)


def row_pattern(formula: str, row: int) -> str:
    """Replace relative references to `row` with '{r}' (used to compare formulas)."""
    parts = []
    for translatable, text in _segments(formula):
        if translatable:
            text = _CELL.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3)}" +
                             ("{r}" if not m.group(3) and int(m.group(4)) == row else m.group(4)), text)
        parts.append(text)
    return "".join(parts)


# Formulas exactly as stored in the reference workbook (row 329), used only when
# no existing formula row can be found to copy from. `[1]` is the workbook's
# external link to ΑΠΟΘΗΚΗ.xlsx and is only valid inside that workbook.
FALLBACK_FORMULAS = {
    "L": ('IFERROR(INDEX([1]!Table4[ΠΟΙΟΤΗΤΑ],MATCH(K{r},[1]!Table4[Νο ΡΟΛΛΟΥ],0)),"-")'),
    "M": 'IFERROR(VLOOKUP(K{r},[1]ΣΒΗΣΜΕΝΑ!$F:$G,2,),"-")',
    "N": 'IFERROR(VLOOKUP(F{r},ID!$A:$B,2,0),"-")',
    "O": 'IFERROR(VLOOKUP(K{r},[1]ΣΒΗΣΜΕΝΑ!$F:$J,5,),"-")',
}
