"""Scanned-document sources (Google Drive, local folder, mock) and page rendering."""

from __future__ import annotations

import io
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image, ImageDraw, ImageFont

SUPPORTED_IMAGE = {"image/jpeg", "image/png", "image/tiff", "image/webp", "image/bmp", "image/gif"}
SUPPORTED = SUPPORTED_IMAGE | {"application/pdf"}


class DocumentSourceError(Exception):
    pass


@dataclass
class RemoteDocument:
    file_id: str
    name: str
    mime_type: str
    uploaded_at: Optional[str]  # ISO
    revision: str  # changes whenever the file content changes
    size: Optional[int] = None
    source: str = "google"


class DocumentSource(Protocol):
    name: str

    def list_latest(self, n: int = 5) -> list[RemoteDocument]: ...

    def download(self, doc: RemoteDocument) -> bytes: ...


class GoogleDriveSource:
    """Google Drive API v3, read-only scope, restricted to one folder."""

    name = "google"
    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

    def __init__(self, folder_id: str, auth: str, credentials_file: Path, token_file: Path,
                 credentials_json: str = ""):
        if not folder_id:
            raise DocumentSourceError("VSPM_DRIVE_FOLDER_ID is not set")
        self.folder_id = folder_id
        try:
            from googleapiclient.discovery import build  # type: ignore
        except ImportError as e:
            raise DocumentSourceError("Google libraries missing: pip install -e .[google]") from e
        if credentials_json:  # Streamlit Cloud: the service-account key comes from the secrets, not a file
            if auth != "service_account":
                raise DocumentSourceError("VSPM_GOOGLE_CREDENTIALS_JSON works only with VSPM_GOOGLE_AUTH=service_account")
            import json

            from google.oauth2 import service_account  # type: ignore
            try:
                info = json.loads(credentials_json)
            except ValueError as e:
                raise DocumentSourceError("VSPM_GOOGLE_CREDENTIALS_JSON is not valid JSON") from e
            creds = service_account.Credentials.from_service_account_info(info, scopes=self.SCOPES)
        else:
            creds = self._credentials(auth, Path(credentials_file), Path(token_file))
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)

    def _credentials(self, auth: str, credentials_file: Path, token_file: Path):
        if not credentials_file.exists():
            raise DocumentSourceError(f"Google credentials file not found: {credentials_file}")
        if auth == "service_account":
            from google.oauth2 import service_account  # type: ignore
            return service_account.Credentials.from_service_account_file(str(credentials_file), scopes=self.SCOPES)
        from google.auth.transport.requests import Request  # type: ignore
        from google.oauth2.credentials import Credentials  # type: ignore
        from google_auth_oauthlib.flow import InstalledAppFlow  # type: ignore
        creds = None
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(str(token_file), self.SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), self.SCOPES)
                creds = flow.run_local_server(port=0)
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json(), encoding="utf-8")
        return creds

    def list_latest(self, n: int = 5) -> list[RemoteDocument]:
        q = (f"'{self.folder_id}' in parents and trashed = false and "
             "(mimeType = 'application/pdf' or mimeType contains 'image/')")
        try:
            resp = self.service.files().list(
                q=q, orderBy="createdTime desc", pageSize=n, supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                fields="files(id,name,mimeType,createdTime,modifiedTime,md5Checksum,headRevisionId,size)",
            ).execute()
        except Exception as e:  # googleapiclient.errors.HttpError and transport errors
            raise DocumentSourceError(f"Google Drive listing failed: {e}") from e
        out = []
        for f in resp.get("files", []):
            rev = f.get("md5Checksum") or f.get("headRevisionId") or f.get("modifiedTime", "")
            out.append(RemoteDocument(f["id"], f["name"], f["mimeType"], f.get("createdTime"), rev,
                                      int(f["size"]) if f.get("size") else None, "google"))
        return out

    def download(self, doc: RemoteDocument) -> bytes:
        try:
            data = self.service.files().get_media(fileId=doc.file_id, supportsAllDrives=True).execute()
        except Exception as e:
            raise DocumentSourceError(f"Download failed for {doc.name}: {e}") from e
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise DocumentSourceError(f"Download of {doc.name} returned no data")
        return bytes(data)

    @classmethod
    def with_service(cls, folder_id: str, service) -> "GoogleDriveSource":
        """Use an already-built Drive service (tests, custom authentication)."""
        obj = cls.__new__(cls)
        obj.folder_id = folder_id
        obj.service = service
        return obj


class LocalFolderSource:
    """Reads scans from a local folder (e.g. a Google Drive for Desktop folder)."""

    name = "local"

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def list_latest(self, n: int = 5) -> list[RemoteDocument]:
        if not self.folder.exists():
            raise DocumentSourceError(f"Scan folder not found: {self.folder}")
        files = []
        for p in self.folder.iterdir():
            mt = mimetypes.guess_type(p.name)[0] or ""
            if p.is_file() and mt in SUPPORTED:
                files.append((p.stat().st_mtime, p, mt))
        files.sort(reverse=True)
        out = []
        for mtime, p, mt in files[:n]:
            st = p.stat()
            ts = datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")
            out.append(RemoteDocument(str(p.resolve()), p.name, mt, ts, f"{st.st_size}-{st.st_mtime_ns}",
                                      st.st_size, "local"))
        return out

    def download(self, doc: RemoteDocument) -> bytes:
        return Path(doc.file_id).read_bytes()


class MockSource:
    """Synthetic handwritten-style production sheets for demos and tests."""

    name = "mock"

    def list_latest(self, n: int = 5) -> list[RemoteDocument]:
        return [RemoteDocument(f"mock-{i}", f"SCAN_MOCK_{i}.png", "image/png", f"2026-10-2{i}T08:0{i}:00+00:00",
                               "rev1", None, "mock") for i in range(1, min(n, 5) + 1)]

    def download(self, doc: RemoteDocument) -> bytes:
        return synthetic_scan_png(doc.name)


def synthetic_scan_png(title: str = "MOCK") -> bytes:
    """A clean fake 'scan' (typed, not handwritten) used only for UI demos."""
    img = Image.new("RGB", (1600, 1000), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype("DejaVuSans.ttf", 30)
        fb = ImageFont.truetype("DejaVuSans-Bold.ttf", 30)
    except OSError:
        f = fb = ImageFont.load_default()
    d.text((60, 40), f"ΔΕΛΤΙΟ ΠΑΡΑΓΩΓΗΣ   ({title} - ΔΟΚΙΜΑΣΤΙΚΟ)", font=fb, fill="black")
    d.text((60, 100), "ΕΠΩΝΥΜΙΑ: ΒΗΤΑΚΗΣ - ΓΑΜΜΑΤΟΥ     ΗΜΕΡ/ΝΙΑ: 21/10/2026", font=f, fill="black")
    d.text((60, 150), "ΕΙΔΟΣ: ΓΑΛΒΑΝΙΖΕ   ΠΑΧΟΣ: 0,60   ΑΡ. ΡΟΛΟΥ: ROLL-T-0001   ΤΟΠΟΘΕΣΙΑ: 7", font=f, fill="black")
    headers = ["ΔΙΑΣΤΑΣΕΙΣ", "ΠΟΣΟΤΗΤΑ", "ΤΕΜΑΧΙΑ", "ΔΕΜΑΤΑ", "BARCODE"]
    xs = [60, 380, 640, 880, 1100]
    y = 240
    for x, h in zip(xs, headers):
        d.text((x, y), h, font=fb, fill="black")
    rows = [["34ΧΡΟΛΟ", "496", "4", "1", "B-101"], ['"', "502", "4", "1", "B-102"], ["50ΧΡΟΛΟ", "730", "3", "1", "B-103"]]
    for r in rows:
        y += 70
        d.line((50, y - 10, 1550, y - 10), fill="#999")
        for x, v in zip(xs, r):
            d.text((x, y), v, font=f, fill="#1a237e")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---- page rendering (preview + AI input) ----------------------------------------------------
def page_images(data: bytes, mime_type: str, max_side: int = 2400, max_pages: int = 10) -> list[Image.Image]:
    if mime_type == "application/pdf":
        import pypdfium2 as pdfium  # type: ignore
        pdf = pdfium.PdfDocument(data)
        out = []
        for i in range(min(len(pdf), max_pages)):
            page = pdf[i]
            w, h = page.get_size()
            scale = min(max_side / max(w, h), 4.0)
            out.append(page.render(scale=scale).to_pil().convert("RGB"))
        return out
    img = Image.open(io.BytesIO(data))
    pages = []
    try:
        for i in range(max_pages):
            img.seek(i)
            pages.append(_fit(img.convert("RGB"), max_side))
    except EOFError:
        pass
    return pages or [_fit(img.convert("RGB"), max_side)]


def _fit(img: Image.Image, max_side: int) -> Image.Image:
    if max(img.size) > max_side:
        r = max_side / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    return img


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
