"""Employee login for the hosted app (Streamlit OIDC: Google or Microsoft accounts).

Fail closed: when login is required (always on the cloud, i.e. when VSPM_DATABASE_URL is
set, unless VSPM_REQUIRE_AUTH=false is set explicitly) nothing of the app is shown until
the person has signed in with an account whose e-mail is in VSPM_ALLOWED_EMAILS. A missing
[auth] configuration or an empty list blocks everyone instead of opening the app.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

import streamlit as st


@dataclass
class Decision:
    status: str  # off | ok | login | denied | misconfigured
    email: str = ""
    name: str = ""
    admin: bool = False
    reason: str = ""


def _emails(text: str) -> set[str]:
    return {e.strip().lower() for e in (text or "").replace(";", ",").split(",") if e.strip()}


def decide(required: bool, auth_configured: bool, user: Mapping | None, allowed: str, admins: str) -> Decision:
    """Pure access decision (unit-tested)."""
    if not required:
        return Decision("off")
    if not auth_configured:
        return Decision("misconfigured", reason="login is required but [auth] is not configured in the secrets")
    allow, adm = _emails(allowed), _emails(admins)
    if not allow and not adm:
        return Decision("misconfigured", reason="VSPM_ALLOWED_EMAILS is empty: nobody may use the app")
    if not user or not user.get("is_logged_in"):
        return Decision("login")
    email = str(user.get("email") or "").strip().lower()
    name = str(user.get("name") or email)
    if not email or user.get("email_verified") is False:
        return Decision("denied", email=email, name=name, reason="the account has no verified e-mail address")
    if email not in allow and email not in adm:
        return Decision("denied", email=email, name=name, reason="this account is not on the list of employees")
    return Decision("ok", email=email, name=name, admin=email in adm)


def _auth_configured() -> bool:
    try:
        auth = st.secrets.get("auth")
    except Exception:  # no secrets file at all
        return False
    return bool(auth) and bool(auth.get("redirect_uri")) and bool(auth.get("cookie_secret"))


def gate(settings) -> Decision:
    """Show the login screen and stop the script unless the person may use the app."""
    from vspm.ui.common import T

    required = settings.require_auth if settings.require_auth is not None else bool(settings.database_url)
    configured = _auth_configured() if required else False
    user = None
    if configured:
        try:
            user = dict(st.user.to_dict()) if hasattr(st.user, "to_dict") else dict(st.user)
        except Exception:
            user = None
    d = decide(required, configured, user, settings.allowed_emails, settings.admin_emails)
    if d.status in ("off", "ok"):
        if d.status == "ok":
            st.session_state["operator"] = d.name or d.email
            st.session_state["auth_email"] = d.email
            st.session_state["auth_admin"] = d.admin
        return d
    st.title("🏷️ VS Production Manager")
    if d.status == "misconfigured":
        st.error(T("auth_misconfigured") + f"\n\n`{d.reason}`")
    elif d.status == "login":
        st.info(T("auth_login_needed"))
        providers = _providers()
        for p in providers or [None]:
            label = T("auth_login") + (f" ({p})" if p and len(providers) > 1 else "")
            if st.button(label, type="primary", key=f"login_{p or 'default'}"):
                st.login(p) if p else st.login()
    else:
        st.error(T("auth_denied", e=d.email or "?") + f"\n\n{d.reason}")
        if st.button(T("auth_logout")):
            st.logout()
    st.stop()
    return d  # pragma: no cover


def _providers() -> list[str]:
    """Named providers in [auth.<name>] (e.g. google, microsoft); [] = single default provider."""
    try:
        auth = st.secrets.get("auth") or {}
    except Exception:
        return []
    return [k for k, v in auth.items() if isinstance(v, Mapping)]


def is_admin() -> bool:
    """Admins manage templates and barcode series. Without login (local install) the local user is admin."""
    from vspm.ui.common import get_context

    s = get_context().settings
    required = s.require_auth if s.require_auth is not None else bool(s.database_url)
    return bool(st.session_state.get("auth_admin")) if required else True


def logged_in_email() -> Optional[str]:
    return st.session_state.get("auth_email")
