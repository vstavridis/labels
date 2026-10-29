"""Streamlit component that prints through the DYMO Web Service of the BROWSER's computer.

The frontend (static/) is plain HTML + compiled TypeScript (frontend/src/bridge.ts)
and DYMO's official dymo.connect.framework.js (static/vendor/, MIT). It is
committed pre-built because Streamlit Community Cloud does not run a JS build.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Optional

import streamlit.components.v1 as components

STATIC_DIR = Path(__file__).parent / "static"
FRAMEWORK = STATIC_DIR / "vendor" / "dymo.connect.framework.js"
EXPECTED_FRAMEWORK_SHA256 = "717ad8a2488d8d936709dc99dde012f4af4d6a407e4d2ba0d4d2ba47e5c43888"

_component = components.declare_component("vspm_dymo_bridge", path=str(STATIC_DIR))


def framework_ok() -> bool:
    """The vendored DYMO framework is present and unmodified."""
    try:
        return hashlib.sha256(FRAMEWORK.read_bytes()).hexdigest() == EXPECTED_FRAMEWORK_SHA256
    except OSError:
        return False


def dymo_bridge(mode: str = "diagnose", *, job: Optional[dict] = None, acked: Optional[list[str]] = None,
                lang: str = "el", rescan_token: str = "", key: str = "dymo_bridge") -> Optional[dict[str, Any]]:
    """Render the bridge. Returns the latest report from the browser (or None):

    {"report_id", "station_id", "station_name", "diagnostics": {...} | None, "results": [job results]}
    """
    return _component(mode=mode, job=job, acked=list(acked or []), lang=lang, rescan_token=rescan_token,
                      key=key, default=None)
