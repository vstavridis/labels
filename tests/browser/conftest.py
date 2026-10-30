"""Real-browser tests of the DYMO bridge.

The Streamlit app runs over HTTPS and Chrome/Edge is told to treat its origin as a
PUBLIC address (--ip-address-space-overrides, the switch Chrome's own Local Network
Access tests use), so the browser applies exactly the rules it applies to the
hosted app on Streamlit Community Cloud. A test double of the DYMO Web Service
listens on https://127.0.0.1:41951 like the real one.

Requires the `playwright` Python package and a Chromium-based browser. Choose the
browser with VSPM_TEST_BROWSER_CHANNEL=chrome|msedge (installed browsers, e.g. on
Windows) - default: Playwright's bundled Chromium.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


def unavailable(msg: str):
    """Skip locally; FAIL in CI jobs that exist to run these tests (VSPM_REQUIRE_BROWSER=1)."""
    if os.environ.get("VSPM_REQUIRE_BROWSER") == "1":
        pytest.fail(msg)
    pytest.skip(msg)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def fake_dymo():
    from vspm.testing_dymo_service import FakeDymoService
    try:
        svc = FakeDymoService(41951).start()
    except OSError as e:
        unavailable(f"port 41951 busy (is a real DYMO service running here?): {e}")
    yield svc
    svc.stop()


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """The real Streamlit app over HTTPS with a self-signed certificate."""
    from vspm.testing import build_test_workbook
    from vspm.testing_dymo_service import self_signed_cert
    d = tmp_path_factory.mktemp("app")
    cert, key = self_signed_cert(d)
    build_test_workbook(d / "data" / "test_workbook" / "w.xlsx")
    port = free_port()
    env = {**os.environ,
           "VSPM_DATA_DIR": str(d / "data"), "VSPM_DRIVE_MODE": "mock", "VSPM_OCR_PROVIDER": "mock",
           "VSPM_BARCODE_MODE": "mock", "VSPM_PRINTER_MODE": "browser", "VSPM_EXCEL_MODE": "direct",
           "VSPM_EXCEL_WRITE_ENABLED": "true", "VSPM_EXCEL_PATH": str(d / "data" / "test_workbook" / "w.xlsx"),
           "VSPM_TEMPLATE_DIR": str(FIXTURES / "labels"), "VSPM_CUSTOMERS_CONFIG": str(FIXTURES / "customers.yaml"),
           "VSPM_REQUIRE_AUTH": "false", "VSPM_LANGUAGE": "el", "PYTHONUTF8": "1"}
    log_path = d / "streamlit.log"
    _SERVER_LOG[:] = [log_path]
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "vspm" / "ui" / "app.py"),
         "--server.port", str(port), "--server.address", "127.0.0.1", "--server.headless", "true",
         "--browser.gatherUsageStats", "false", "--server.sslCertFile", str(cert), "--server.sslKeyFile", str(key),
         "--server.fileWatcherType", "none"],
        # A log file, not a pipe: nobody reads a pipe here, and once its buffer is full (small on
        # Windows) the server blocks on its next log line and stops answering.
        env=env, stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT))
    url = f"https://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(url + "/_stcore/health", verify=False, timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(log_path.read_text(encoding="utf-8", errors="replace"))
        time.sleep(0.5)
    else:
        proc.terminate()
        raise RuntimeError("Streamlit did not start")
    yield {"url": url, "port": port, "data": d / "data", "env": env}
    proc.terminate()
    try:
        proc.wait(10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()


@pytest.fixture(scope="session")
def browser(app_server):
    channel = os.environ.get("VSPM_TEST_BROWSER_CHANNEL") or None
    args = ["--no-proxy-server",
            # Treat the app like a site on the internet (Streamlit Cloud), so Chrome's
            # Local Network Access rules for public -> loopback requests apply.
            f"--ip-address-space-overrides=127.0.0.1:{app_server['port']}=public"]
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch(channel=channel, args=args)
        except Exception as e:
            unavailable(f"browser not available ({channel or 'bundled chromium'}): {e}")
        yield b
        b.close()


@pytest.fixture
def dymo_reset(fake_dymo):
    httpx.post("https://127.0.0.1:41951/__test/reset", verify=False)
    yield fake_dymo


_OPEN_CONTEXTS: list = []
_SERVER_LOG: list = []


def new_page(browser, app_server, allow_local_network: bool):
    ctx = browser.new_context(ignore_https_errors=True, locale="el-GR")
    _OPEN_CONTEXTS.append(ctx)
    if allow_local_network:
        # What the employee does once when Chrome/Edge asks: "Allow".
        ctx.grant_permissions(["local-network-access"], origin=app_server["url"])
    page = ctx.new_page()
    page.set_default_timeout(45000)
    return ctx, page


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """On a failure, print what every open page showed and the end of the server log (CI diagnosis)."""
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call" and rep.failed:
        out = []
        for ctx in list(_OPEN_CONTEXTS):
            try:
                for i, page in enumerate(ctx.pages):
                    out.append(f"--- page {i} {page.url}")
                    out.append(f"errors: {page.locator('[data-testid=\"stException\"]').all_inner_texts()}")
                    out.append(f"alerts: {page.locator('[data-testid=\"stAlert\"]').all_inner_texts()[:10]}")
                    out.append(page.locator("body").inner_text()[:2500])
            except Exception as e:  # context already closed
                out.append(f"(page not readable: {e})")
        if _SERVER_LOG and _SERVER_LOG[0].exists():
            out.append("--- server log (end)")
            out.append(_SERVER_LOG[0].read_text(encoding="utf-8", errors="replace")[-3000:])
        rep.sections.append(("browser state at failure", "\n".join(out)))
    if rep.when == "teardown":
        _OPEN_CONTEXTS.clear()

