"""Employee login decision (fail closed)."""

from vspm.ui.auth import decide

ALLOWED = "Maria@Example.com, nikos@example.com"
ADMINS = "boss@example.com"


def user(email, logged=True, verified=True):
    return {"is_logged_in": logged, "email": email, "name": email.split("@")[0], "email_verified": verified}


def test_not_required_is_open():
    assert decide(False, False, None, "", "").status == "off"


def test_required_but_not_configured_blocks_everyone():
    assert decide(True, False, user("maria@example.com"), ALLOWED, ADMINS).status == "misconfigured"


def test_empty_allowlist_blocks_everyone():
    assert decide(True, True, user("maria@example.com"), "", "").status == "misconfigured"


def test_not_logged_in_must_login():
    assert decide(True, True, None, ALLOWED, ADMINS).status == "login"
    assert decide(True, True, user("maria@example.com", logged=False), ALLOWED, ADMINS).status == "login"


def test_allowed_employee_case_insensitive():
    d = decide(True, True, user("MARIA@example.com"), ALLOWED, ADMINS)
    assert d.status == "ok" and d.email == "maria@example.com" and not d.admin


def test_stranger_and_unverified_are_denied():
    assert decide(True, True, user("someone@gmail.com"), ALLOWED, ADMINS).status == "denied"
    assert decide(True, True, user("maria@example.com", verified=False), ALLOWED, ADMINS).status == "denied"


def test_admin_may_log_in_and_is_admin():
    d = decide(True, True, user("boss@example.com"), ALLOWED, ADMINS)
    assert d.status == "ok" and d.admin


# ---- the real app entry point --------------------------------------------------------------
import pytest  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.conftest import ROOT  # noqa: E402

APP = str(ROOT / "src" / "vspm" / "ui" / "app.py")


@pytest.fixture
def app_env(settings, monkeypatch):
    for k, v in {"VSPM_DATA_DIR": str(settings.data_dir), "VSPM_DRIVE_MODE": "mock", "VSPM_OCR_PROVIDER": "mock",
                 "VSPM_BARCODE_MODE": "mock", "VSPM_PRINTER_MODE": "mock",
                 "VSPM_TEMPLATE_DIR": str(settings.template_dir), "VSPM_TEMPLATES_CONFIG": str(settings.templates_config),
                 "VSPM_CUSTOMERS_CONFIG": str(settings.customers_config), "VSPM_LANGUAGE": "en",
                 "VSPM_DATABASE_URL": settings.database_url, "VSPM_ALLOWED_EMAILS": "maria@example.com"}.items():
        monkeypatch.setenv(k, v)
    from vspm.ui.common import get_context
    get_context.clear()
    yield
    get_context.clear()


def test_app_stays_closed_when_login_required_but_not_configured(app_env, monkeypatch):
    monkeypatch.setenv("VSPM_REQUIRE_AUTH", "true")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("not configured" in e.value for e in at.error), [e.value for e in at.error]
    assert not at.sidebar.text_input and not at.selectbox  # no page content, no operator field


def test_app_opens_when_login_explicitly_off(app_env, monkeypatch):
    monkeypatch.setenv("VSPM_REQUIRE_AUTH", "false")
    at = AppTest.from_file(APP, default_timeout=120)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert not any("not configured" in e.value for e in at.error)
    assert at.sidebar.text_input  # operator field of the local (no-login) mode


def test_blank_require_auth_means_default(settings, monkeypatch):
    from vspm.config import Settings
    monkeypatch.setenv("VSPM_REQUIRE_AUTH", "")
    assert Settings(_env_file=None).require_auth is None  # -> required whenever a database URL is set
