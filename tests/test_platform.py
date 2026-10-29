"""Windows-related robustness: .env with BOM, cloud-synced data folder, console encoding."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from vspm.config import Settings
from vspm.envfile import read_env


def test_env_file_saved_with_bom_is_read(tmp_path):
    env = tmp_path / ".env"
    # Windows Notepad (older versions) saves UTF-8 with a BOM.
    env.write_bytes("﻿VSPM_LANGUAGE=en\nVSPM_EXCEL_SHEET=΄Ετοιμα προϊόντα\n".encode("utf-8"))
    s = Settings(_env_file=env)
    assert s.language == "en" and s.excel_sheet == "΄Ετοιμα προϊόντα"
    assert read_env(env)["VSPM_LANGUAGE"] == "en"


def test_env_example_is_valid(tmp_path):
    from dotenv import dotenv_values
    root = Path(__file__).resolve().parents[1]
    values = dotenv_values(root / ".env.example")
    leaked = {k: v for k, v in values.items() if v and "#" in v}
    assert not leaked, f"inline comments parsed as values: {leaked}"
    s = Settings(_env_file=root / ".env.example")
    assert s.operator_name == "" and s.drive_folder_id == "" and s.computer_name == ""
    assert s.excel_write_enabled is False and s.excel_require_test_copy is True
    assert s.barcode_mode.value == "disabled" and s.template_files == ""
    assert s.cups_options == "-o fit-to-page"


def test_data_folder_inside_onedrive_is_reported(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path / "OneDrive - Company" / "VSPM" / "data")
    assert "cloud-synced" in s.synced_folder_warning()
    assert Settings(_env_file=None, data_dir=tmp_path / "VSPM" / "data").synced_folder_warning() is None


def test_cli_output_survives_legacy_console_encoding(tmp_path):
    env = {**os.environ, "PYTHONIOENCODING": "cp1252", "PYTHONUTF8": "0"}
    code = "import vspm.cli, sys; print('Ετικέτα ΓΑΛΒΑΝΙΖΕ'); sys.stdout.flush()"
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True)
    assert r.returncode == 0, r.stderr.decode(errors="replace")
    assert "Ετικέτα".encode("utf-8") in r.stdout
