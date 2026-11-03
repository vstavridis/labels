"""Scanned-document sources (Google Drive, local folder, mock) and page rendering."""

from __future__ import annotations

import threading
import time

import io
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol

from PIL import Image, ImageDraw, ImageFont, ImageOps

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


SA_FIELDS = ("type", "client_email", "private_key", "token_uri")


def parse_service_account_json(raw: str) -> dict:
    """Read a Google service-account key pasted into the Streamlit secrets.

    Accepted: the JSON exactly as downloaded, inside  (recommended) or """ """ - in the
    latter TOML turns the \\n of the private key into real line breaks, which strict JSON
    refuses; they are accepted here. Errors say where the problem is, never the key itself."""
    import json

    text = (raw or "").strip()
    if len(text) >= 2 and text[0] in "'\"" and text[-1] == text[0] and text[1:].lstrip().startswith("{"):
        text = text[1:-1].strip()
    if not text.startswith("{"):
        raise DocumentSourceError(
            "VSPM_GOOGLE_CREDENTIALS_JSON must contain the whole service-account JSON file, starting with { . "
            "In the Streamlit secrets write:  VSPM_GOOGLE_CREDENTIALS_JSON = ''' (new line) <paste the file> "
            "(new line) '''")
    try:
        info = json.loads(text, strict=False)  # strict=False: real line breaks inside the private key
    except ValueError as e:
        line, col = getattr(e, "lineno", "?"), getattr(e, "colno", "?")
        raise DocumentSourceError(
            f"VSPM_GOOGLE_CREDENTIALS_JSON is not valid JSON (line {line}, column {col}: {getattr(e, 'msg', e)}). "
            "Paste the downloaded .json file unchanged between ''' and ''' (three single quotes), "
            "not between \"...\" and without editing it.") from e
    if not isinstance(info, dict):
        raise DocumentSourceError("VSPM_GOOGLE_CREDENTIALS_JSON must be a JSON object (the key file).")
    missing = [f for f in SA_FIELDS if not info.get(f)]
    if missing or info.get("type") != "service_account":
        raise DocumentSourceError(
            "VSPM_GOOGLE_CREDENTIALS_JSON is not a service-account key (missing: "
            f"{', '.join(missing) or 'type=service_account'}). Create it under Google Cloud → IAM → Service "
            "accounts → Keys → Add key → JSON (an OAuth client file does not work here).")
    return info


class GoogleDriveSource:
    """Google Drive API v3, read-only scope, restricted to one folder."""

    name = "google"
    SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
    WRITE_SCOPES = ["https://www.googleapis.com/auth/drive"]  # only to write barcodes onto the scans

    def __init__(self, folder_id: str, auth: str, credentials_file: Path, token_file: Path,
                 credentials_json: str = "", recursive: bool = False, file_types: Optional[list[str]] = None,
                 write: bool = False):
        self.recursive = recursive
        self.write = write
        self.file_types = file_types
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
            from google.oauth2 import service_account  # type: ignore
            info = parse_service_account_json(credentials_json)
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=self.WRITE_SCOPES if write else self.SCOPES)
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

    FOLDER_MIME = "application/vnd.google-apps.folder"
    FILE_TYPES = {"jpg": ["image/jpeg"], "jpeg": ["image/jpeg"], "png": ["image/png"], "pdf": ["application/pdf"],
                  "tif": ["image/tiff"], "tiff": ["image/tiff"], "heic": ["image/heic"]}
    BATCH = 30  # folders per query ('a' in parents or 'b' in parents ...)
    TREE_TTL = 300.0  # seconds the folder tree is reused

    recursive: bool = False
    file_types: Optional[list[str]] = None
    _tree: Optional[dict[str, str]] = None
    _tree_at: float = 0.0

    def _mime_clause(self) -> str:
        if not self.file_types:
            return "(mimeType = 'application/pdf' or mimeType contains 'image/')"
        mimes: list[str] = []
        for t in self.file_types:
            for m in self.FILE_TYPES.get(t.strip().lower().lstrip("."), []):
                if m not in mimes:
                    mimes.append(m)
        if not mimes:
            raise DocumentSourceError(f"Unknown file types {self.file_types} (use e.g. jpg, png, pdf)")
        return "(" + " or ".join(f"mimeType = '{m}'" for m in mimes) + ")"

    def _list(self, q: str, order: Optional[str], page_size: int, max_items: Optional[int] = None) -> list[dict]:
        out: list[dict] = []
        token = None
        while True:
            kw = dict(q=q, pageSize=page_size, supportsAllDrives=True, includeItemsFromAllDrives=True,
                      fields="nextPageToken,files(id,name,mimeType,parents,createdTime,modifiedTime,md5Checksum,"
                             "headRevisionId,size)")
            if order:
                kw["orderBy"] = order
            if token:
                kw["pageToken"] = token
            try:
                resp = self.service.files().list(**kw).execute()
            except Exception as e:  # googleapiclient.errors.HttpError and transport errors
                raise DocumentSourceError(f"Google Drive listing failed: {e}") from e
            out += resp.get("files", [])
            token = resp.get("nextPageToken")
            if not token or (max_items is not None and len(out) >= max_items):
                return out

    def folder_tree(self, max_folders: int = 5000) -> dict[str, str]:
        """{folder id: path below the scans folder} for the folder and all its subfolders."""
        if self._tree is not None and time.monotonic() - self._tree_at < self.TREE_TTL:
            return self._tree
        paths = {self.folder_id: ""}
        frontier = [self.folder_id]
        while frontier and len(paths) < max_folders:
            nxt: list[str] = []
            for i in range(0, len(frontier), self.BATCH):
                batch = frontier[i:i + self.BATCH]
                parents = " or ".join(f"'{fid}' in parents" for fid in batch)
                for f in self._list(f"({parents}) and mimeType = '{self.FOLDER_MIME}' and trashed = false", None,
                                    1000):
                    if f["id"] in paths:
                        continue
                    parent = next((p for p in f.get("parents", []) if p in paths), batch[0])
                    paths[f["id"]] = (paths[parent] + "/" if paths[parent] else "") + f["name"]
                    nxt.append(f["id"])
            frontier = nxt
        self._tree, self._tree_at = paths, time.monotonic()
        return paths

    def list_latest(self, n: int = 5) -> list[RemoteDocument]:
        """The n newest scans (by upload time) in the folder - and, if recursive, in all its subfolders."""
        tree = self.folder_tree() if self.recursive else {self.folder_id: ""}
        ids = list(tree)
        found: list[dict] = []
        for i in range(0, len(ids), self.BATCH):
            parents = " or ".join(f"'{fid}' in parents" for fid in ids[i:i + self.BATCH])
            found += self._list(f"({parents}) and trashed = false and {self._mime_clause()}", "createdTime desc",
                                n, max_items=n)
        found.sort(key=lambda f: f.get("createdTime") or "", reverse=True)
        out = []
        for f in found[:n]:
            folder = next((tree[p] for p in f.get("parents", []) if p in tree), "")
            rev = f.get("md5Checksum") or f.get("headRevisionId") or f.get("modifiedTime", "")
            out.append(RemoteDocument(f["id"], f"{folder}/{f['name']}" if folder else f["name"], f["mimeType"],
                                      f.get("createdTime"), rev, int(f["size"]) if f.get("size") else None, "google"))
        return out

    def download(self, doc: RemoteDocument) -> bytes:
        try:
            data = self.service.files().get_media(fileId=doc.file_id, supportsAllDrives=True).execute()
        except Exception as e:
            raise DocumentSourceError(f"Download failed for {doc.name}: {e}") from e
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise DocumentSourceError(f"Download of {doc.name} returned no data")
        return bytes(data)

    # ---- writing barcodes onto a scan (service account with Editor role on the folder) ---------------
    def file_meta(self, file_id: str) -> dict:
        try:
            return self.service.files().get(fileId=file_id, supportsAllDrives=True,
                                            fields="id,name,mimeType,md5Checksum,headRevisionId").execute()
        except Exception as e:
            raise DocumentSourceError(f"Google Drive: cannot read the file details ({e})") from e

    def keep_revision_forever(self, file_id: str, revision_id: str) -> None:
        try:
            self.service.revisions().update(fileId=file_id, revisionId=revision_id,
                                            body={"keepForever": True}).execute()
        except Exception as e:
            raise DocumentSourceError(f"Google Drive: cannot keep the original version ({e}). The service "
                                      "account needs the Editor role on the scans folder.") from e

    def download_revision(self, file_id: str, revision_id: str) -> bytes:
        try:
            data = self.service.revisions().get_media(fileId=file_id, revisionId=revision_id).execute()
        except Exception as e:
            raise DocumentSourceError(f"Google Drive: cannot read the original version ({e})") from e
        return bytes(data)

    def replace_content(self, file_id: str, data: bytes, mime_type: str) -> dict:
        from googleapiclient.http import MediaIoBaseUpload  # type: ignore
        try:
            return self.service.files().update(
                fileId=file_id, supportsAllDrives=True, fields="md5Checksum,headRevisionId",
                media_body=MediaIoBaseUpload(io.BytesIO(data), mimetype=mime_type, resumable=False)).execute()
        except Exception as e:
            raise DocumentSourceError(f"Google Drive: cannot save the photo ({e}). The service account needs "
                                      "the Editor role on the scans folder.") from e

    @classmethod
    def with_service(cls, folder_id: str, service, recursive: bool = False,
                     file_types: Optional[list[str]] = None) -> "GoogleDriveSource":
        """Use an already-built Drive service (tests, custom authentication)."""
        obj = cls.__new__(cls)
        obj.folder_id = folder_id
        obj.service = service
        obj.recursive = recursive
        obj.file_types = file_types
        obj.write = False
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
_PDFIUM_LOCK = threading.Lock()  # PDFium is not thread-safe; Streamlit runs each session in its own thread


def page_images(data: bytes, mime_type: str, max_side: int = 2400, max_pages: int = 10) -> list[Image.Image]:
    if mime_type == "application/pdf":
        import pypdfium2 as pdfium  # type: ignore
        with _PDFIUM_LOCK:
            pdf = pdfium.PdfDocument(data)
            try:
                out = []
                for i in range(min(len(pdf), max_pages)):
                    page = pdf[i]
                    w, h = page.get_size()
                    scale = min(max_side / max(w, h), 4.0)
                    out.append(page.render(scale=scale).to_pil().convert("RGB"))
            finally:
                pdf.close()
        return out
    img = Image.open(io.BytesIO(data))
    pages = []
    try:
        for i in range(max_pages):
            img.seek(i)
            # Phone photos store their rotation in EXIF: apply it so the sheet is upright.
            pages.append(_fit(ImageOps.exif_transpose(img).convert("RGB"), max_side))
    except EOFError:
        pass
    return pages or [_fit(ImageOps.exif_transpose(img).convert("RGB"), max_side)]


def _fit(img: Image.Image, max_side: int) -> Image.Image:
    if max(img.size) > max_side:
        r = max_side / max(img.size)
        img = img.resize((int(img.width * r), int(img.height * r)), Image.LANCZOS)
    return img


def to_jpeg(img: Image.Image, quality: int = 90) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
