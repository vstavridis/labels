"""Page F - Settings, customer rules and diagnostics."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from vspm.config import PROJECT_ROOT
from vspm.domain.models import TemplateKey
from vspm.envfile import read_env, update_env
from vspm.services.customers import CustomerRule
from vspm.ui.common import T, flash, get_context, reload_context, show_flash

ENV_FIELDS = [
    ("VSPM_DRIVE_MODE", ["google", "local", "mock"]),
    ("VSPM_DRIVE_FOLDER_ID", None),
    ("VSPM_GOOGLE_AUTH", ["service_account", "oauth"]),
    ("VSPM_GOOGLE_CREDENTIALS_FILE", None),
    ("VSPM_LOCAL_SCAN_DIR", None),
    ("VSPM_OCR_PROVIDER", ["anthropic", "mock", "disabled"]),
    ("VSPM_OCR_MODEL", None),
    ("VSPM_OCR_EFFORT", ["low", "medium", "high", "xhigh", "max"]),
    ("VSPM_OCR_UPLOAD_CONSENT", ["false", "true"]),
    ("VSPM_PRINTER_MODE", ["mock", "dymo_web", "dymo_com"]),
    ("VSPM_PRINTER_NAME", None),
    ("VSPM_DYMO_WEB_URL", None),
    ("VSPM_TEMPLATE_DIR", None),
    ("VSPM_TEMPLATES_CONFIG", None),
    ("VSPM_BARCODE_MODE", ["disabled", "mock", "pilot_range", "registry"]),
    ("VSPM_REGISTRY_URL", None),
    ("VSPM_PILOT_RANGES", None),
    ("VSPM_EXCEL_PATH", None),
    ("VSPM_EXCEL_SHEET", None),
    ("VSPM_EXCEL_MODE", ["staging", "direct", "registry_queue"]),
    ("VSPM_EXCEL_WRITE_ENABLED", ["false", "true"]),
    ("VSPM_EXCEL_DATE_FORMAT", None),
    ("VSPM_TIMEZONE", None),
    ("VSPM_LOG_LEVEL", ["INFO", "DEBUG", "WARNING"]),
]
SECRETS = ["ANTHROPIC_API_KEY", "VSPM_REGISTRY_API_KEY"]


def render() -> None:
    ctx = get_context()
    show_flash()
    st.title(T("nav_settings"))
    tabs = st.tabs([T("env_settings"), T("customer_rules"), T("integration_checks"), T("logs")])
    env_path = PROJECT_ROOT / ".env"

    with tabs[0]:
        st.caption(f"{env_path} — {T('restart_note')}")
        current = read_env(env_path)
        effective = ctx.settings.describe()
        new: dict[str, str] = {}
        with st.form("env"):
            cols = st.columns(2)
            for i, (key, options) in enumerate(ENV_FIELDS):
                attr = key.removeprefix("VSPM_").lower()
                val = current.get(key, effective.get(attr, ""))
                col = cols[i % 2]
                if options:
                    val_l = str(val).lower() if options[0].islower() else str(val)
                    new[key] = col.selectbox(key, options, index=options.index(val_l) if val_l in options else 0)
                else:
                    new[key] = col.text_input(key, value=str(val))
            st.markdown("**Secrets** (left empty = unchanged)")
            for key in SECRETS:
                v = st.text_input(f"{key} ({'set' if current.get(key) else 'not set'})", type="password")
                if v:
                    new[key] = v
            if st.form_submit_button(T("save"), type="primary"):
                update_env(env_path, new)
                flash("success", T("saved") + " " + T("restart_note"))
                st.rerun()
        if st.button("🔄 " + T("reload")):
            from vspm.config import get_settings
            get_settings.cache_clear()
            reload_context()
            st.rerun()

    with tabs[1]:
        st.caption("Χαμηλότερη προτεραιότητα = ελέγχεται πρώτη. match: exact | contains | regex. "
                   "Αν κανένας κανόνας δεν ταιριάζει: VS (λατινικά ονόματα → πρόταση EX με επιβεβαίωση).")
        rules = ctx.customers.rules()
        df = pd.DataFrame([{
            "id": r.id, "priority": r.priority, "match": r.match_type, "pattern": r.pattern,
            "template": r.template_key.value if r.template_key else None, "short_name": r.short_name,
            "confirm": r.requires_confirmation, "active": r.active, "notes": r.notes} for r in rules],
            columns=["id", "priority", "match", "pattern", "template", "short_name", "confirm", "active", "notes"])
        edited = st.data_editor(df, num_rows="dynamic", hide_index=True, use_container_width=True, column_config={
            "id": None,
            "match": st.column_config.SelectboxColumn(options=["exact", "contains", "regex"]),
            "template": st.column_config.SelectboxColumn(options=[k.value for k in TemplateKey]),
        }, key=f"rules{len(rules)}")
        if st.button(T("save"), key="save_rules", type="primary"):
            try:
                kept = set()
                for _, r in edited.iterrows():
                    if not str(r["pattern"] or "").strip():
                        continue
                    rule = CustomerRule(
                        id=None if pd.isna(r["id"]) else int(r["id"]), priority=int(r["priority"] or 100),
                        match_type=r["match"] or "exact", pattern=str(r["pattern"]).strip(),
                        template_key=TemplateKey(r["template"]) if r["template"] else None,
                        short_name=str(r["short_name"] or "").strip(), requires_confirmation=bool(r["confirm"]),
                        active=bool(r["active"]) if not pd.isna(r["active"]) else True, notes=str(r["notes"] or ""))
                    if rule.id is None:
                        kept.add(ctx.customers.add_rule(rule))
                    else:
                        ctx.customers.update_rule(rule)
                        kept.add(rule.id)
                for r in rules:
                    if r.id not in kept:
                        ctx.customers.delete_rule(r.id)
                ctx.db.audit("customer_rules", "-", "saved", {"count": len(kept)}, st.session_state.get("operator"))
                flash("success", T("saved"))
            except Exception as e:
                flash("error", str(e))
            st.rerun()
        name = st.text_input(T("test_customer"))
        if name:
            sel = ctx.customers.select(name)
            st.info(f"→ **{sel.template_key.value}**, ΠΕΛΑΤΗΣ: **{sel.short_name}**, script: {sel.script}, "
                    f"confirm: {sel.needs_confirmation}\n\n{sel.reason}")

    with tabs[2]:
        if st.button(T("integration_checks"), type="primary"):
            ok, msg = ctx.printer.status()
            (st.success if ok else st.error)(f"DYMO ({ctx.settings.printer_mode.value}): {msg}")
            try:
                st.write([p.__dict__ for p in ctx.printer.list_printers()])
            except Exception as e:
                st.warning(f"printers: {e}")
            ok, msg = ctx.allocator.health()
            (st.success if ok else st.error)(f"Barcodes ({ctx.settings.barcode_mode.value}): {msg}")
            probs = ctx.writer.preflight()
            (st.success if not probs else st.error)(f"Excel: {ctx.writer.path} {'OK' if not probs else probs}")
            if ctx.documents:
                try:
                    n = len(ctx.documents.source.list_latest(5))
                    st.success(f"Documents ({ctx.settings.drive_mode.value}): {n}")
                except Exception as e:
                    st.error(f"Documents: {e}")
            for k, v in ctx.problems.items():
                st.warning(f"{k}: {v}")
        st.json(ctx.settings.describe(), expanded=False)

    with tabs[3]:
        log = ctx.settings.data_path / "logs" / "vspm.jsonl"
        if log.exists():
            lines = log.read_text(encoding="utf-8").splitlines()[-200:]
            st.code("\n".join(reversed(lines)), language="json")
        st.dataframe([dict(r) for r in ctx.db.query(
            "SELECT ts, actor, computer, entity, entity_id, action, details_json FROM audit_log ORDER BY id DESC "
            "LIMIT 300")], hide_index=True, use_container_width=True)
