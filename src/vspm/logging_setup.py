"""Structured (JSON-lines) logging to data/logs plus readable console output."""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime, timezone

from vspm.config import Settings

_configured = False


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        d = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            d["exc"] = self.formatException(record.exc_info)
        for k, v in getattr(record, "ctx", {}).items():
            d[k] = v
        return json.dumps(d, ensure_ascii=False, default=str)


def setup_logging(s: Settings) -> None:
    global _configured
    if _configured:
        return
    log_dir = s.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(s.log_level.upper())
    fh = logging.handlers.RotatingFileHandler(log_dir / "vspm.jsonl", maxBytes=5_000_000, backupCount=10,
                                              encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    root.addHandler(fh)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(ch)
    _configured = True
