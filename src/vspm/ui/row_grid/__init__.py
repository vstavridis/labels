"""Editable table of the production rows (Streamlit custom component, plain JavaScript in static/).

Everything happens in the browser: typing, ⬇ fill (row 1's value into every unprinted row), adding and
deleting rows. Nothing is sent to the server until the operator presses the save button, so editing
never reloads the page.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import streamlit.components.v1 as components

_component = components.declare_component("vspm_row_grid", path=str(Path(__file__).parent / "static"))


def row_grid(rows: list[dict], columns: list[dict], *, texts: dict[str, str], version: str,
             key: str) -> Optional[dict[str, Any]]:
    """rows: [{"id", "#", "printed", "flags", <column keys>...}]; columns: [{"key", "label", "fill",
    "options"?, "width"?}]. Returns {"action": "save", "rows": [...], "deleted": [ids], "click": n} after a
    press of the save button, else None. `version` resets the table to `rows` when it changes."""
    return _component(rows=rows, columns=columns, texts=texts, version=version, key=key, default=None)
