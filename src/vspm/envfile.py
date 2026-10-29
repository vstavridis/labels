"""Update KEY=value lines in a .env file while keeping comments and order."""

from __future__ import annotations

import re
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=\s*(.*)$", line)
        if m:
            val = re.sub(r"\s+#.*$", "", m.group(2)).strip().strip('"').strip("'")
            out[m.group(1)] = val
    return out


def update_env(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*([A-Z0-9_]+)\s*=", line)
        if m and m.group(1) in values:
            key = m.group(1)
            lines[i] = f"{key}={_quote(values[key])}"
            done.add(key)
    for k, v in values.items():
        if k not in done:
            lines.append(f"{k}={_quote(v)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote(v: str) -> str:
    v = str(v)
    if v == "" or re.search(r"[\s#'\"]", v):
        return '"' + v.replace('"', '\\"') + '"'
    return v
