"""Customer-name normalization and DYMO template selection.

Rules are data (YAML seed + editable rows in SQLite), evaluated by explicit
priority (lower number first). The default VS classification applies only
when no rule matches. Latin-script names are *suggested* as EX but always
require operator confirmation, because the existing workbook contains
Latin-named VS and PG customers as well.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from vspm.db.database import Database, utcnow
from vspm.domain.models import TemplateKey

# Latin letters that look identical to Greek capitals (and vice versa).
LATIN_TO_GREEK = str.maketrans({
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ", "M": "Μ",
    "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "Y": "Υ", "X": "Χ",
})
GREEK_TO_LATIN = str.maketrans({v: k for k, v in {
    "A": "Α", "B": "Β", "E": "Ε", "Z": "Ζ", "H": "Η", "I": "Ι", "K": "Κ", "M": "Μ",
    "N": "Ν", "O": "Ο", "P": "Ρ", "T": "Τ", "Y": "Υ", "X": "Χ",
}.items()})

DASHES = "‐‑‒–—―−"


def script_profile(text: str) -> str:
    """'greek', 'latin', 'mixed' or 'none' based on letters present."""
    g = l = 0
    for ch in text:
        if not ch.isalpha():
            continue
        name = unicodedata.name(ch, "")
        if "GREEK" in name:
            g += 1
        elif "LATIN" in name:
            l += 1
    if g == 0 and l == 0:
        return "none"
    if g and l:
        return "mixed"
    return "greek" if g else "latin"


def strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if unicodedata.category(c) != "Mn"))


def clean_display(text: str) -> str:
    """Whitespace/dash cleanup that keeps the text otherwise unchanged."""
    t = unicodedata.normalize("NFC", text or "")
    for d in DASHES:
        t = t.replace(d, "-")
    return re.sub(r"\s+", " ", t).strip()


def normalize_key(text: str) -> str:
    """Comparison key: upper-case, no accents/punctuation, single spaces and
    homoglyphs folded into the name's dominant script."""
    t = strip_accents(unicodedata.normalize("NFKC", text or "")).upper()
    for d in DASHES:
        t = t.replace(d, "-")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"[_\s]+", " ", t).strip()
    greek = sum(1 for c in t if "GREEK" in unicodedata.name(c, ""))
    latin = sum(1 for c in t if "LATIN" in unicodedata.name(c, ""))
    return t.translate(LATIN_TO_GREEK) if greek >= latin else t.translate(GREEK_TO_LATIN)


@dataclass
class CustomerRule:
    id: Optional[int]
    priority: int
    match_type: str  # exact | contains | regex
    pattern: str
    template_key: Optional[TemplateKey]
    short_name: str
    requires_confirmation: bool = False
    active: bool = True
    notes: str = ""

    def matches(self, name: str) -> bool:
        if not self.active or not name.strip():
            return False
        key = normalize_key(name)
        if self.match_type == "exact":
            return key == normalize_key(self.pattern)
        if self.match_type == "contains":
            pk = normalize_key(self.pattern)
            return bool(pk) and re.search(rf"(^|\s){re.escape(pk)}(\s|$)", key) is not None
        if self.match_type == "regex":
            return re.search(self.pattern, key) is not None
        raise ValueError(f"Unknown match type {self.match_type}")


@dataclass
class Selection:
    template_key: TemplateKey
    short_name: str
    reason: str
    needs_confirmation: bool
    script: str
    rule_id: Optional[int] = None
    short_name_mapped: bool = False


class CustomerService:
    def __init__(self, db: Database, seed_file: Path | None = None):
        self.db = db
        if seed_file and seed_file.exists() and not self.db.one("SELECT id FROM customer_rules LIMIT 1"):
            self.import_yaml(seed_file)

    # ---- rule storage ------------------------------------------------------------
    def import_yaml(self, path: Path) -> int:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        n = 0
        for r in data.get("rules", []):
            self.add_rule(CustomerRule(
                id=None, priority=int(r["priority"]), match_type=r.get("match", "exact"), pattern=r["pattern"],
                template_key=TemplateKey(r["template"]) if r.get("template") else None,
                short_name=r.get("short_name", "") or "", requires_confirmation=bool(r.get("requires_confirmation")),
                notes=r.get("notes", "") or ""))
            n += 1
        return n

    def add_rule(self, rule: CustomerRule) -> int:
        if rule.match_type == "regex":
            re.compile(rule.pattern)
        now = utcnow()
        return self.db.execute(
            "INSERT INTO customer_rules (priority, match_type, pattern, template_key, short_name, requires_confirmation, "
            "active, notes, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (rule.priority, rule.match_type, rule.pattern, rule.template_key.value if rule.template_key else None,
             rule.short_name, int(rule.requires_confirmation), int(rule.active), rule.notes, now, now))

    def update_rule(self, rule: CustomerRule) -> None:
        if rule.match_type == "regex":
            re.compile(rule.pattern)
        self.db.execute(
            "UPDATE customer_rules SET priority=?, match_type=?, pattern=?, template_key=?, short_name=?, "
            "requires_confirmation=?, active=?, notes=?, updated_at=? WHERE id=?",
            (rule.priority, rule.match_type, rule.pattern, rule.template_key.value if rule.template_key else None,
             rule.short_name, int(rule.requires_confirmation), int(rule.active), rule.notes, utcnow(), rule.id))

    def delete_rule(self, rule_id: int) -> None:
        self.db.execute("DELETE FROM customer_rules WHERE id=?", (rule_id,))

    def rules(self, include_inactive: bool = True) -> list[CustomerRule]:
        rows = self.db.query("SELECT * FROM customer_rules ORDER BY priority, id")
        out = [CustomerRule(
            id=r["id"], priority=r["priority"], match_type=r["match_type"], pattern=r["pattern"],
            template_key=TemplateKey(r["template_key"]) if r["template_key"] else None, short_name=r["short_name"],
            requires_confirmation=bool(r["requires_confirmation"]), active=bool(r["active"]), notes=r["notes"])
            for r in rows]
        return out if include_inactive else [r for r in out if r.active]

    # ---- selection -------------------------------------------------------------
    def select(self, customer_full: str) -> Selection:
        name = clean_display(customer_full)
        script = script_profile(name)
        if not name:
            return Selection(TemplateKey.VS, "", "no customer name recognised", True, script)
        template: Optional[TemplateKey] = None
        short: Optional[str] = None
        reasons: list[str] = []
        confirm = False
        rule_id = None
        for rule in self.rules(include_inactive=False):
            if not rule.matches(name):
                continue
            if template is None and rule.template_key is not None:
                template = rule.template_key
                rule_id = rule.id
                confirm = confirm or rule.requires_confirmation
                reasons.append(f"rule #{rule.id} ({rule.match_type} '{rule.pattern}') -> {template.value}")
            if short is None and rule.short_name:
                short = rule.short_name
            if template is not None and short is not None:
                break
        if template is None:
            if script == "latin":
                template, confirm = TemplateKey.EX, True
                reasons.append("Latin-script name: EX suggested, confirmation required")
            elif script == "mixed":
                template, confirm = TemplateKey.VS, True
                reasons.append("name mixes Greek and Latin letters: check spelling and label type")
            else:
                template = TemplateKey.VS
                reasons.append("no specific rule matched: default VS")
        return Selection(template_key=template, short_name=short if short is not None else name,
                         reason="; ".join(reasons), needs_confirmation=confirm, script=script, rule_id=rule_id,
                         short_name_mapped=short is not None)
