"""Reproduce a Streamlit Community Cloud start and check the app works.

Streamlit Cloud installs only requirements.txt (the `vspm` package itself is NOT
installed) and runs `streamlit run src/vspm/ui/app.py` from the repository root.
This script does the same and drives the result with a real browser:

  1. with login required but no [auth] secrets -> the app must stay closed;
  2. with login off -> every page renders without an exception, the DYMO
     browser component loads DYMO's framework (integrity check passes), the
     database tables are created and barcode printing is closed until a
     series is initialised.

Usage (CI job "Streamlit Cloud deployment"):
    python scripts/check_cloud_deploy.py --database-url postgresql://...

Needs `pip install -r requirements.txt playwright` and a Chromium for Playwright.
Uses mock documents/AI and never touches a printer, DYMO counter or workbook.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGES = ["dashboard", "recognition", "review", "print", "history", "printer", "settings"]


def start(port: int, database_url: str, require_auth: str | None, log: Path) -> subprocess.Popen:
    env = {**os.environ,
           "VSPM_DATABASE_URL": database_url, "VSPM_BARCODE_MODE": "database", "VSPM_PRINTER_MODE": "browser",
           "VSPM_EXCEL_MODE": "staging", "VSPM_EXCEL_WRITE_ENABLED": "false", "VSPM_DRIVE_MODE": "mock",
           "VSPM_OCR_PROVIDER": "mock", "VSPM_LANGUAGE": "el", "VSPM_ALLOWED_EMAILS": "employee@example.com",
           "PYTHONPATH": ""}  # like Streamlit Cloud: nothing but requirements.txt is importable
    env.pop("VSPM_REQUIRE_AUTH", None)
    if require_auth is not None:
        env["VSPM_REQUIRE_AUTH"] = require_auth
    cmd = [sys.executable, "-m", "streamlit", "run", "src/vspm/ui/app.py", "--server.port", str(port),
           "--server.headless", "true", "--browser.gatherUsageStats", "false"]
    proc = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=log.open("wb"), stderr=subprocess.STDOUT)
    for _ in range(120):
        try:
            if urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2).status == 200:
                return proc
        except OSError:
            pass
        if proc.poll() is not None:
            break
        time.sleep(0.5)
    raise SystemExit(f"Streamlit did not start on port {port}:\n{log.read_text(errors='replace')[-3000:]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--log-dir", default=str(ROOT / "data" / "deploy-check"))
    a = ap.parse_args()
    logs = Path(a.log_dir)
    logs.mkdir(parents=True, exist_ok=True)

    try:
        import vspm  # noqa: F401
        print("WARNING: the vspm package is importable here; Streamlit Cloud does not install it.")
    except ImportError:
        print("OK  vspm package not installed (same as Streamlit Cloud)")

    from playwright.sync_api import sync_playwright

    failures: list[str] = []
    closed = start(8611, a.database_url, None, logs / "login_required.log")
    opened = start(8612, a.database_url, "false", logs / "login_off.log")
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(60000)

            page.goto("http://127.0.0.1:8611/")
            page.get_by_text("η εφαρμογή παραμένει κλειστή").first.wait_for()
            if page.locator('[data-testid="stSidebarNav"] a').count():
                failures.append("login required: navigation is visible")
            print("OK  login required without [auth] secrets -> app closed")

            for path in PAGES:
                page.goto(f"http://127.0.0.1:8612/{path}")
                page.locator("h1").first.wait_for()
                page.wait_for_timeout(2500)
                errors = page.locator('[data-testid="stException"]').all_inner_texts()
                if errors:
                    failures.append(f"page {path}: {errors[0][:200]}")
                print(f"{'OK ' if not errors else 'ERR'} page {path}: {page.locator('h1').first.inner_text()}")

            page.goto("http://127.0.0.1:8612/printer")
            page.locator('iframe[title*="vspm_dymo_bridge"]').first.wait_for()
            frame = next((f for f in page.frames if "vspm_dymo_bridge" in f.url), None)
            if frame is None:
                failures.append("DYMO bridge component not loaded")
            else:
                frame.locator('[data-testid="service"]').wait_for()
                if not frame.evaluate("typeof dymo !== 'undefined' && !!dymo.label.framework"):
                    failures.append("DYMO framework not loaded inside the component")
                print("OK  DYMO browser component and framework loaded")
            page.wait_for_timeout(2000)
            if page.get_by_text("missing or modified").count():
                failures.append("dymo.connect.framework.js integrity check failed")

            page.goto("http://127.0.0.1:8612/settings")
            page.get_by_role("tab", name="Σειρές barcode").click()
            page.get_by_text("Καμία σειρά δεν έχει οριστεί").first.wait_for()
            print("OK  barcode printing closed until a series is initialised")
            browser.close()
    finally:
        for proc in (closed, opened):
            proc.terminate()
    for log in logs.glob("*.log"):
        text = log.read_text(errors="replace")
        if "Traceback" in text:
            failures.append(f"{log.name}: server traceback\n{text[-2000:]}")
    if failures:
        print("\nFAILED:\n- " + "\n- ".join(failures))
        return 1
    print("\nDeployment check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
