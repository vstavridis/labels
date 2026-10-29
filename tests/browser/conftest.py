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
import tempfile
import time
from pathlib import Path

import httpx
import pytest

pw = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


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
        pytest.skip(f"port 41951 busy (is a real DYMO service running here?): {e}")
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
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", str(ROOT / "src" / "vspm" / "ui" / "app.py"),
         "--server.port", str(port), "--server.address", "127.0.0.1", "--server.headless", "true",
         "--browser.gatherUsageStats", "false", "--server.sslCertFile", str(cert), "--server.sslKeyFile", str(key),
         "--server.fileWatcherType", "none"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=str(ROOT))
    url = f"https://127.0.0.1:{port}"
    for _ in range(120):
        try:
            if httpx.get(url + "/_stcore/health", verify=False, timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if proc.poll() is not None:
            raise RuntimeError(proc.stdout.read().decode(errors="replace"))
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
            pytest.skip(f"browser not available ({channel or 'bundled chromium'}): {e}")
        yield b
        b.close()


@pytest.fixture
def dymo_reset(fake_dymo):
    httpx.post("https://127.0.0.1:41951/__test/reset", verify=False)
    yield fake_dymo


def new_page(browser, app_server, allow_local_network: bool):
    ctx = browser.new_context(ignore_https_errors=True, locale="el-GR")
    if allow_local_network:
        # What the employee does once when Chrome/Edge asks: "Allow".
        ctx.grant_permissions(["local-network-access"], origin=app_server["url"])
    page = ctx.new_page()
    page.set_default_timeout(45000)
    return ctx, page
