"""Production domain models (Pydantic)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class TemplateKey(str, Enum):
    VS = "VS"
    PG = "PG"
    PG0 = "PG0"
    EX = "EX"


class RowStatus(str, Enum):
    draft = "draft"
    ocr_complete = "ocr_complete"
    needs_review = "needs_review"
    approved = "approved"
    barcode_reserved = "barcode_reserved"
    print_submitted = "print_submitted"
    print_unconfirmed = "print_unconfirmed"
    printed_confirmed = "printed_confirmed"
    error = "error"


class PrintStatus(str, Enum):
    not_printed = "not_printed"
    barcode_reserved = "barcode_reserved"
    print_submitted = "print_submitted"
    print_unconfirmed = "print_unconfirmed"
    printed_confirmed = "printed_confirmed"
    print_failed_confirmed = "print_failed_confirmed"
    error = "error"


class ExcelStatus(str, Enum):
    not_queued = "not_queued"
    pending = "pending"
    staged = "staged"
    queued_remote = "queued_remote"
    synchronized = "synchronized"
    conflict = "conflict"
    error = "error"


class DocumentStatus(str, Enum):
    new = "new"
    downloaded = "downloaded"
    ocr_complete = "ocr_complete"
    in_review = "in_review"
    partially_printed = "partially_printed"
    completed = "completed"
    error = "error"


# ---------------------------------------------------------------------------
# OCR extraction models (returned by any OCR provider)
# ---------------------------------------------------------------------------

class ExtractedValue(BaseModel):
    """One recognised value. `raw` is exactly what was read; never invented."""

    raw: Optional[str] = Field(
        default=None, description="Text exactly as written (null if blank/unreadable)."
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertain: bool = Field(default=False, description="True if ambiguous/unclear.")
    is_ditto: bool = Field(
        default=False,
        description="True if the cell contains a ditto mark (\" or 〃 or -//-) or is blank "
        "below a value that clearly repeats.",
    )
    note: Optional[str] = None


class ExtractedRow(BaseModel):
    row_number: int = Field(description="1-based order of the row on the document.")
    page: int = 1
    customer: ExtractedValue = Field(default_factory=ExtractedValue)  # ΕΠΩΝΥΜΙΑ
    material: ExtractedValue = Field(default_factory=ExtractedValue)  # ΕΙΔΟΣ
    thickness: ExtractedValue = Field(default_factory=ExtractedValue)  # ΠΑΧΟΣ
    dimensions: ExtractedValue = Field(default_factory=ExtractedValue)  # ΔΙΑΣΤΑΣΕΙΣ
    weight: ExtractedValue = Field(default_factory=ExtractedValue)  # ΠΟΣΟΤΗΤΑ (kg)
    pieces: ExtractedValue = Field(default_factory=ExtractedValue)  # ΤΕΜΑΧΙΑ
    packages: ExtractedValue = Field(default_factory=ExtractedValue)  # ΔΕΜΑΤΑ
    source_barcode: ExtractedValue = Field(default_factory=ExtractedValue)  # BARCODE
    source_roll: ExtractedValue = Field(default_factory=ExtractedValue)  # ΑΡ. ΡΟΛΟΥ
    location: ExtractedValue = Field(default_factory=ExtractedValue)  # ΤΟΠΟΘΕΣΙΑ
    document_date: ExtractedValue = Field(default_factory=ExtractedValue)  # ΗΜΕΡ/ΝΙΑ


class ExtractedDocument(BaseModel):
    """Document-level fields apply to every row unless the row overrides them."""

    customer: ExtractedValue = Field(default_factory=ExtractedValue)
    material: ExtractedValue = Field(default_factory=ExtractedValue)
    thickness: ExtractedValue = Field(default_factory=ExtractedValue)
    dimensions: ExtractedValue = Field(default_factory=ExtractedValue)
    source_roll: ExtractedValue = Field(default_factory=ExtractedValue)
    location: ExtractedValue = Field(default_factory=ExtractedValue)
    document_date: ExtractedValue = Field(default_factory=ExtractedValue)
    rows: list[ExtractedRow] = Field(default_factory=list)
    page_count: int = 1
    warnings: list[str] = Field(default_factory=list)


ROW_FIELDS = (
    "customer",
    "material",
    "thickness",
    "dimensions",
    "weight",
    "pieces",
    "packages",
    "source_barcode",
    "source_roll",
    "location",
    "document_date",
)

# Fields that may be written once at document level and inherited by rows.
INHERITABLE_FIELDS = (
    "customer",
    "material",
    "thickness",
    "dimensions",
    "source_roll",
    "location",
    "document_date",
)


# ---------------------------------------------------------------------------
# Review / production models
# ---------------------------------------------------------------------------

class ProductionRow(BaseModel):
    id: Optional[int] = None
    document_id: Optional[int] = None
    row_index: int = 1
    customer_full: str = ""
    customer_short: str = ""
    material: str = ""
    thickness: str = ""
    dimensions: str = ""
    weight: str = ""
    pieces: str = ""
    packages: str = ""
    source_barcode: str = ""
    source_roll: str = ""
    location: str = ""
    document_date: str = ""
    template_key: Optional[TemplateKey] = None
    template_suggested: Optional[TemplateKey] = None
    template_reason: str = ""
    template_needs_confirmation: bool = False
    flags: dict[str, str] = Field(default_factory=dict)
    ocr_original: dict[str, Optional[str]] = Field(default_factory=dict)
    approved: bool = False
    status: RowStatus = RowStatus.draft
    label_id: Optional[int] = None


class LabelContent(BaseModel):
    """The exact values printed on a label (snapshot used for reprints)."""

    template_key: TemplateKey
    material: str
    thickness: str
    dimensions: str
    pieces: str
    customer_short: str
    weight: str
    barcode: str
    printed_at_local: Optional[datetime] = None


class ExcelRowPayload(BaseModel):
    """Values for one row of the target worksheet (columns A..P)."""

    material: str  # A
    thickness: str  # B
    dimensions: str  # C
    pieces: str  # D
    weight: str  # E
    customer: str  # F
    category: str  # G (VS / PG / EX)
    barcode: str  # H
    location: str  # I
    # J (ΘΕΣΗ) intentionally left untouched
    roll: str  # K
    print_date: str  # P (ISO date YYYY-MM-DD, local factory timezone)
