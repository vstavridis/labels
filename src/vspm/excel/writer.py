"""Safe, idempotent appends to the production workbook.

Safety measures (none of them alone is sufficient with OneDrive):
  1. Refuse if the file is open in Excel on this PC (owner file `~$...` or an
     exclusive-open test on Windows), is an online-only OneDrive placeholder,
     or if OneDrive conflict copies exist next to it.
  2. Idempotency: the generated barcode (column H) is the key. If it is
     already in the sheet, nothing is written and the existing row is reported.
  3. Optimistic concurrency: the file hash is taken before editing and checked
     again just before replacing; any change aborts the write.
  4. A timestamped backup of the previous file is kept.
  5. Atomic replace (temp file in the same folder + os.replace), then the file
     is read back and the written row is verified cell by cell.
These checks reduce, but cannot eliminate, the risk of conflicting with an
edit another employee makes in Excel Online / another PC that has not synced
to this PC yet. See docs/EXCEL_ONEDRIVE.md.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from vspm.domain.models import ExcelRowPayload
from vspm.excel.xlsx_package import (
    SheetEditor, XlsxError, XlsxPackage, save_package, set_full_calc_on_load,
)

FORMULA_COLS = ("L", "M", "N", "O")


class ExcelSyncError(Exception):
    status = "error"


class WorkbookLockedError(ExcelSyncError):
    status = "conflict"


class WorkbookChangedError(ExcelSyncError):
    status = "conflict"


class WorkbookConflictError(ExcelSyncError):
    status = "conflict"


class WorkbookUnavailableError(ExcelSyncError):
    status = "error"


class ExcelWritesDisabledError(ExcelSyncError):
    status = "error"


@dataclass
class AppendResult:
    barcode: str
    status: str  # written | already_present
    row_number: int


@dataclass
class WriteReport:
    results: list[AppendResult]
    hash_before: str
    hash_after: str
    backup_path: Optional[Path]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _int_text(v: str) -> bool:
    return bool(re.fullmatch(r"[1-9]\d{0,14}|0", v))


def payload_to_cells(p: ExcelRowPayload) -> tuple[dict[str, object], dict[str, date]]:
    """Map the payload to columns following the workbook's existing value types."""

    def num_or_text(v: str) -> object:
        v = v.strip()
        if _int_text(v):
            return int(v)
        if re.fullmatch(r"\d+[.,]\d+", v):
            return float(v.replace(",", "."))
        return v

    values: dict[str, object] = {
        "A": p.material,
        "B": p.thickness,                      # text "0.60", as in existing rows
        "C": p.dimensions,
        "D": int(p.pieces) if _int_text(p.pieces) else p.pieces,  # "2+1" stays text
        "E": num_or_text(p.weight),
        "F": p.customer,
        "G": p.category,
        "H": p.barcode,
        "I": int(p.location) if _int_text(p.location) else p.location,
        # J (ΘΕΣΗ) is never written.
        "K": int(p.roll) if _int_text(p.roll) else p.roll,
    }
    return values, {"P": date.fromisoformat(p.print_date)}


class SafeWorkbookWriter:
    def __init__(self, path: Path, sheet: str, *, backup_dir: Path, date_format: str = "dd/mm/yyyy",
                 full_calc_on_load: bool = True, block_on_conflict_copies: bool = True,
                 write_enabled: bool = False, keep_backups: int = 200,
                 replace_retries: int = 6, retry_delay: float = 0.25, replace_func=os.replace,
                 allowed_root: Optional[Path] = None):
        self.path = Path(path)
        self.sheet = sheet
        self.backup_dir = Path(backup_dir)
        self.date_format = date_format
        self.full_calc_on_load = full_calc_on_load
        self.block_on_conflict_copies = block_on_conflict_copies
        self.write_enabled = write_enabled
        self.keep_backups = keep_backups
        self.replace_retries = replace_retries
        self.retry_delay = retry_delay
        self._replace = replace_func
        self.allowed_root = Path(allowed_root).resolve() if allowed_root else None

    # ---- environment checks --------------------------------------------------------
    def is_test_copy(self) -> bool:
        if self.allowed_root is None:
            return True
        try:
            self.path.resolve().relative_to(self.allowed_root)
            return True
        except ValueError:
            return False

    def owner_files(self) -> list[Path]:
        name = self.path.name
        cands = {"~$" + name, "~$" + name[1:], "~$" + name[2:]}
        return [self.path.parent / c for c in cands if (self.path.parent / c).exists()]

    def conflict_copies(self) -> list[Path]:
        stem, suf = self.path.stem, self.path.suffix
        out = []
        for p in self.path.parent.glob(f"{glob_escape(stem)}-*{suf}"):
            if p != self.path:
                out.append(p)
        return out

    def is_placeholder(self) -> bool:
        if sys.platform != "win32":
            return False
        attrs = getattr(os.stat(self.path), "st_file_attributes", 0)
        FILE_ATTRIBUTE_OFFLINE = 0x1000
        RECALL_ON_OPEN = 0x40000
        RECALL_ON_DATA_ACCESS = 0x400000
        return bool(attrs & (FILE_ATTRIBUTE_OFFLINE | RECALL_ON_OPEN | RECALL_ON_DATA_ACCESS))

    def exclusive_open_ok(self) -> bool:
        if sys.platform != "win32":
            return True
        try:
            fd = os.open(self.path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        except PermissionError:
            return False
        os.close(fd)
        return True

    def preflight(self) -> list[str]:
        """Return blocking problems (empty list = OK to try writing)."""
        problems = []
        if not self.path.exists():
            return [f"Workbook not found: {self.path}"]
        if self.is_placeholder():
            problems.append("The workbook is an online-only OneDrive file. Right-click it and choose "
                            "'Always keep on this device'.")
        owners = self.owner_files()
        if owners:
            problems.append(f"The workbook appears to be open in Excel on this PC ({owners[0].name}). Close it.")
        if not self.exclusive_open_ok():
            problems.append("The workbook is locked by another program (probably Excel). Close it.")
        copies = self.conflict_copies()
        if copies and self.block_on_conflict_copies:
            problems.append("OneDrive conflict copies exist and must be resolved first: "
                            + ", ".join(p.name for p in copies))
        return problems

    # ---- read-only access ---------------------------------------------------------------
    def read_bytes(self) -> bytes:
        try:
            return self.path.read_bytes()
        except PermissionError as e:
            raise WorkbookLockedError(f"Cannot read workbook (locked): {e}") from e
        except OSError as e:
            raise WorkbookUnavailableError(f"Cannot read workbook: {e}") from e

    def find_barcodes(self, barcodes: list[str]) -> dict[str, list[int]]:
        """Read-only lookup of barcodes in column H (used for reconciliation)."""
        editor = SheetEditor(XlsxPackage(self.read_bytes()), self.sheet)
        values = editor.column_values("H")
        wanted = {b.strip().upper(): b for b in barcodes}
        found: dict[str, list[int]] = {b: [] for b in barcodes}
        for row, v in values.items():
            key = str(v).strip().upper()
            if key in wanted:
                found[wanted[key]].append(row)
        return found

    # ---- writing --------------------------------------------------------------------------
    def append(self, payloads: list[ExcelRowPayload]) -> WriteReport:
        if not self.write_enabled:
            raise ExcelWritesDisabledError(
                "Excel writing is disabled (VSPM_EXCEL_WRITE_ENABLED=false). Use staging or enable it after "
                "completing the test-copy checklist.")
        if self.allowed_root is not None and not self.is_test_copy():
            raise ExcelWritesDisabledError(
                f"Pilot safety: writing is only allowed to a test copy inside {self.allowed_root} "
                f"(VSPM_EXCEL_REQUIRE_TEST_COPY=true), not to {self.path}. Use `vspm make-test-workbook`.")
        problems = self.preflight()
        if problems:
            exc = WorkbookConflictError if any("conflict" in p for p in problems) else WorkbookLockedError
            if not self.path.exists():
                exc = WorkbookUnavailableError
            raise exc(" ".join(problems))

        original = self.read_bytes()
        hash_before = sha256(original)
        try:
            pkg = XlsxPackage(original)
            editor = SheetEditor(pkg, self.sheet)
        except XlsxError as e:
            raise WorkbookUnavailableError(str(e)) from e

        results: list[AppendResult] = []
        to_write: list[tuple[int, ExcelRowPayload]] = []
        existing_h = editor.column_values("H")
        existing_index = {str(v).strip().upper(): r for r, v in existing_h.items()}
        next_row = editor.last_data_row() + 1
        template_row = next_row - 1
        for p in payloads:
            key = p.barcode.strip().upper()
            if key in existing_index:
                results.append(AppendResult(p.barcode, "already_present", existing_index[key]))
                continue
            # Target row must be empty in A..P (it may carry formatting only).
            for col in "ABCDEFGHIJKLMNOP":
                cv = editor.value(next_row, col)
                if editor._has_value(cv) and cv.kind != "formula":
                    raise WorkbookConflictError(f"Target row {next_row} is not empty (column {col}).")
            to_write.append((next_row, p))
            existing_index[key] = next_row
            results.append(AppendResult(p.barcode, "written", next_row))
            next_row += 1

        if not to_write:
            return WriteReport(results, hash_before, hash_before, None)

        for rnum, p in to_write:
            values, dates = payload_to_cells(p)
            formulas = {c: editor.formula_for_row(c, rnum) for c in FORMULA_COLS}
            editor.write_row(rnum, values, formulas, dates, self.date_format, template_row)
        if self.full_calc_on_load:
            set_full_calc_on_load(pkg)
        new_bytes = save_package(pkg)
        self._verify(new_bytes, to_write)

        # Optimistic concurrency check right before replacing.
        if sha256(self.read_bytes()) != hash_before:
            raise WorkbookChangedError("The workbook changed while the row was being prepared. Nothing was written; "
                                       "retry.")
        backup = self._backup(original)
        self._atomic_replace(new_bytes, hash_before)

        written = self.read_bytes()
        hash_after = sha256(written)
        if hash_after != sha256(new_bytes):
            raise WorkbookChangedError("Read-back hash differs from what was written (file changed immediately "
                                       "after writing - check OneDrive).")
        self._verify(written, to_write)
        return WriteReport(results, hash_before, hash_after, backup)

    def _atomic_replace(self, new_bytes: bytes, hash_before: str) -> None:
        """Write a temp file next to the workbook and rename it over the workbook.

        On Windows the rename fails while any program holds the file open (Excel,
        but also briefly OneDrive, antivirus or Explorer's preview). Short-lived
        locks are retried; before every retry the workbook is checked again so a
        version saved by someone else in the meantime is never overwritten.
        """
        tmp = self.path.parent / f".vspm-tmp-{uuid.uuid4().hex}{self.path.suffix}"
        try:
            tmp.write_bytes(new_bytes)
            for attempt in range(self.replace_retries + 1):
                try:
                    self._replace(tmp, self.path)
                    return
                except PermissionError as e:
                    if attempt == self.replace_retries:
                        raise WorkbookLockedError(
                            f"Could not replace the workbook after {attempt + 1} attempts - it is open in another "
                            f"program (Excel, OneDrive sync, antivirus). Nothing was written. ({e})") from e
                    time.sleep(self.retry_delay * (2 ** attempt))
                    if sha256(self.read_bytes()) != hash_before:
                        raise WorkbookChangedError(
                            "The workbook changed while waiting for it to be released. Nothing was written; retry.")
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def _verify(self, data: bytes, written: list[tuple[int, ExcelRowPayload]]) -> None:
        editor = SheetEditor(XlsxPackage(data), self.sheet)
        for rnum, p in written:
            values, dates = payload_to_cells(p)
            for col, expected in values.items():
                got = editor.value(rnum, col)
                exp_empty = expected is None or expected == ""
                if exp_empty:
                    if editor._has_value(got):
                        raise ExcelSyncError(f"Verification failed at {col}{rnum}: expected empty")
                    continue
                if got.value != expected and str(got.value) != str(expected):
                    raise ExcelSyncError(f"Verification failed at {col}{rnum}: {got.value!r} != {expected!r}")
            for col in FORMULA_COLS:
                got = editor.value(rnum, col)
                if got.kind != "formula" or not got.formula:
                    raise ExcelSyncError(f"Verification failed: no formula in {col}{rnum}")
            if editor.find_rows_with_value("H", p.barcode) != [rnum]:
                raise ExcelSyncError(f"Verification failed: barcode {p.barcode} not unique at row {rnum}")

    def _backup(self, data: bytes) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        dest = self.backup_dir / f"{self.path.stem}.{ts}{self.path.suffix}"
        dest.write_bytes(data)
        backups = sorted(self.backup_dir.glob(f"{glob_escape(self.path.stem)}.*{self.path.suffix}"))
        for old in backups[: max(0, len(backups) - self.keep_backups)]:
            old.unlink(missing_ok=True)
        return dest


def glob_escape(s: str) -> str:
    return re.sub(r"([\[\]*?])", r"[\1]", s)
