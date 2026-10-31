"""Settings -> Cloud sources: OneDrive templates + workbook, Google Drive scans folder."""

from __future__ import annotations

import json

import streamlit as st

from vspm.integrations.onedrive import AuthorizationPending, NotConnected, OneDriveError
from vspm.services.cloud_sources import parse_drive_folder_id
from vspm.ui.auth import is_admin
from vspm.ui.common import T, flash, operator, reload_context

FIELDS = [("onedrive_templates_url", "cs_templates_url"), ("onedrive_template_files", "cs_template_files"),
          ("onedrive_workbook_url", "cs_workbook_url"), ("onedrive_workbook_test_url", "cs_workbook_test_url"),
          ("gdrive_folder_url", "cs_gdrive_url")]


def service_account_email(settings) -> str:
    raw = settings.google_credentials_json
    if not raw:
        try:
            raw = settings.resolve(settings.google_credentials_file).read_text(encoding="utf-8")
        except OSError:
            return ""
    try:
        return json.loads(raw).get("client_email", "")
    except ValueError:
        return ""


def _run(label: str, fn):
    """Run an action and show its error in plain words (never a stack trace or a token)."""
    try:
        return fn()
    except AuthorizationPending as e:
        st.info(str(e))
    except NotConnected as e:
        st.warning(f"{label}: {e}")
    except (OneDriveError, ValueError) as e:
        st.error(f"{label}: {e}")
    except Exception as e:  # noqa: BLE001 - unexpected; type only
        st.error(f"{label}: {type(e).__name__}: {str(e)[:300]}")
    return None


def render(ctx) -> None:
    cloud = ctx.cloud
    admin = is_admin()
    st.caption(T("cs_intro"))
    if not admin:
        st.info(T("admin_only"))

    # ---- links -------------------------------------------------------------------------------
    with st.form("cloud_links"):
        values = {}
        for key, label in FIELDS:
            values[key] = st.text_input(T(label), value=cloud.config.get(key), disabled=not admin,
                                        type="default")
        if st.form_submit_button(T("save"), type="primary", disabled=not admin):
            try:
                parse_drive_folder_id(values["gdrive_folder_url"])
                for key, v in values.items():
                    if v.strip() != cloud.config.get(key):
                        cloud.config.set(key, v, operator())
                flash("success", T("saved"))
                reload_context()
            except ValueError as e:
                flash("error", str(e))
            st.rerun()

    # ---- Microsoft account ------------------------------------------------------------------
    st.subheader("Microsoft OneDrive")
    od = cloud.onedrive
    acct = od.account()
    s = ctx.settings
    c = st.columns(3)
    c[0].metric(T("cs_ms_account"), acct["account"] if acct else "—")
    c[1].metric("VSPM_MS_CLIENT_ID", "✅" if s.ms_client_id else "❌")
    c[2].metric("VSPM_SECRET_KEY", "✅" if s.secret_key else "❌")
    if admin:
        b = st.columns(3)
        if b[0].button(T("cs_connect"), disabled=not (s.ms_client_id and s.secret_key)):
            code = _run("Microsoft", od.auth.start_device_login)
            if code:
                st.session_state["ms_device_code"] = code
        code = st.session_state.get("ms_device_code")
        if code:
            st.info(T("cs_device_help", u=code.verification_uri, c=code.user_code))
            if b[1].button(T("cs_signed_in"), type="primary"):
                account = _run("Microsoft", lambda: od.auth.finish_device_login(code))
                if account:
                    st.session_state.pop("ms_device_code", None)
                    ctx.db.audit("cloud", "microsoft", "connected", {"account": account}, operator())
                    flash("success", T("cs_connected", a=account))
                    st.rerun()
        if acct and b[2].button(T("cs_disconnect")):
            od.disconnect(operator())
            st.rerun()

    # ---- templates ------------------------------------------------------------------------------
    st.markdown("**" + T("cs_templates") + "**")
    b = st.columns(2)
    if b[0].button(T("cs_test_onedrive"), disabled=not admin):
        def test():
            folder = od.resolve("templates_folder", cloud.config.get("onedrive_templates_url"))
            names = [f.name for f in od.files.children(folder.drive_id, folder.item_id)
                     if f.name.lower().endswith(".label")]
            return folder, names
        res = _run("OneDrive", test)
        if res:
            st.success(T("cs_folder_ok", f=res[0].name, n=len(res[1])))
            st.write(res[1])
    if b[1].button(T("cs_refresh_templates"), disabled=not admin, type="primary"):
        res = _run("OneDrive", lambda: cloud.templates.refresh(operator(), force=True))
        if res:
            for r in res:
                (st.success if r.status != "error" else st.error)(
                    f"{r.key}: {r.filename or '—'} — {r.status}" + (f" — {r.error}" if r.error else "")
                    + (f" — SHA-256 {r.sha256[:12]}…" if r.sha256 else ""))
    last = cloud.templates.last_refresh()
    if last:
        st.caption(T("cs_last_refresh", t=str(last.get("at", ""))[:19], ok="✅" if last.get("ok") else "❌")
                   + (f" — {last['error']}" if last.get("error") else ""))
    rows = ctx.db.query("SELECT role, name, etag, sha256, last_sync, last_error FROM cloud_files WHERE role LIKE "
                        "'template:%' OR role='templates_folder' ORDER BY role")
    if rows:
        st.dataframe([{"": r["role"], T("cs_file"): r["name"], "SHA-256": (r["sha256"] or "")[:12],
                       T("cs_last_sync"): (r["last_sync"] or "")[:19], T("cs_error"): r["last_error"] or ""}
                      for r in rows], hide_index=True, width="stretch")

    # ---- workbook ---------------------------------------------------------------------------------
    st.markdown("**" + T("cs_workbook") + "**")
    wb = cloud.workbook
    st.caption(T("cs_excel_mode", m=s.excel_mode.value,
                 p=T("cs_prod_on") if wb.production_enabled() else T("cs_prod_off")))
    b = st.columns(3)
    if b[0].button(T("cs_test_workbook"), disabled=not admin):
        info = _run("Excel", lambda: wb.inspect("workbook"))
        if info:
            st.success(T("cs_workbook_ok", **info))
    if b[1].button(T("cs_test_copy"), disabled=not admin):
        info = _run("Excel", lambda: wb.inspect("workbook_test"))
        if info:
            st.success(T("cs_workbook_ok", **info))
    if b[2].button(T("cs_validate"), disabled=not admin):
        _run("Excel", lambda: wb.validate_copy(operator()))
    v = wb.validation()
    if v:
        (st.success if v.get("ok") else st.error)(T("cs_validation", ok="✅" if v.get("ok") else "❌",
                                                    t=str(v.get("at", ""))[:19], n=v.get("name", "—")))
        st.dataframe([{"check": c[0], "ok": "✅" if c[1] else "❌", "": c[2]} for c in v.get("checks", [])],
                     hide_index=True, width="stretch")
    if admin:
        enabled = wb.production_enabled()
        want = st.toggle(T("cs_prod_toggle"), value=enabled, disabled=not v.get("ok") and not enabled)
        if want != enabled:
            try:
                wb.set_production(want, operator())
                st.rerun()
            except OneDriveError as e:
                st.error(str(e))
        if st.button(T("cs_sync_now")):
            res = _run("Excel", lambda: wb.sync_pending(operator()))
            if res:
                (st.success if res.status in ("synchronized", "nothing") else st.warning)(res.message)
    backups = wb.backups()
    if backups and admin:
        with st.expander(T("cs_backups", n=len(backups))):
            for bk in backups[:5]:
                name, data = wb.backup_bytes(bk["id"])
                st.download_button(f"{bk['created_at'][:19]} — {bk['reason']}", data, file_name=name,
                                   key=f"bk{bk['id']}")

    # ---- Google Drive ------------------------------------------------------------------------------
    st.subheader("Google Drive")
    try:
        fid = cloud.gdrive_folder_id() or s.drive_folder_id
    except ValueError as e:
        fid = ""
        st.error(str(e))
    sa = service_account_email(s)
    st.caption(T("cs_gdrive_info", f=fid or "—", m=s.drive_mode.value, sa=sa or "—"))
    b = st.columns(2)
    if b[0].button(T("cs_test_gdrive"), disabled=not admin):
        def test():
            if ctx.documents is None:
                raise OneDriveError(ctx.problems.get("documents", "Google Drive is not configured"))
            return ctx.documents.source.list_latest(5)
        docs = _run("Google Drive", test)
        if docs is not None:
            st.success(T("cs_gdrive_ok", n=len(docs)))
            st.dataframe([{"name": d.name, "uploaded": d.uploaded_at, "type": d.mime_type} for d in docs],
                         hide_index=True, width="stretch")
        elif sa:
            st.caption(T("cs_gdrive_share", sa=sa))
    if b[1].button(T("cs_refresh_scans")):
        docs = _run("Google Drive", lambda: ctx.documents.latest(5)) if ctx.documents else None
        if docs is not None:
            st.success(T("cs_gdrive_ok", n=len(docs)))
