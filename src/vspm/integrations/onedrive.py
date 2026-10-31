"""Microsoft personal OneDrive through Microsoft Graph (file APIs only).

What is supported for a personal Microsoft account (and used here):

* Sign-in: OAuth 2.0 **device code flow** against the ``consumers`` endpoint with a public
  client (Entra app registration, "Personal Microsoft accounts", "Allow public client flows").
  The admin signs in once at https://microsoft.com/devicelogin; the app keeps the **refresh
  token encrypted** in the database and refreshes access tokens itself. Delegated scopes:
  ``Files.ReadWrite offline_access User.Read`` (personal OneDrive has no per-folder scope).
* A sharing link (``https://1drv.ms/...``) is **not** an authorisation. It is only used to find
  the item: ``GET /shares/u!{base64url(link)}/driveItem`` called **with the owner's token**.
  The resolved drive id + item id are stored and used from then on.
* Files: ``GET /drives/{d}/items/{i}`` (metadata, eTag/cTag), ``/children`` (folder listing),
  ``/content`` (download; Graph answers with a redirect to a pre-authenticated URL),
  ``PUT /content`` with ``If-Match: <eTag>`` (replace the whole file only if nobody saved it in
  the meantime; HTTP 412 otherwise).
* NOT used: the Graph Excel workbook API (/workbook/...) - it does not support personal OneDrive.

Nothing here logs tokens or file contents.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
LOGIN = "https://login.microsoftonline.com"
SCOPES = "offline_access Files.ReadWrite User.Read"
ONEDRIVE_HOSTS = ("1drv.ms", "onedrive.live.com", "onedrive.com", "d.docs.live.net")


class OneDriveError(Exception):
    """A problem the operator can act on (message is safe to show)."""


class NotConnected(OneDriveError):
    pass


class AuthorizationPending(OneDriveError):
    pass


class PreconditionFailed(OneDriveError):
    """The file changed since it was read (ETag mismatch)."""


class FileLocked(OneDriveError):
    pass


# ---- URLs --------------------------------------------------------------------------------------
def is_onedrive_url(url: str) -> bool:
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in ONEDRIVE_HOSTS)


def share_id(url: str) -> str:
    """Graph sharing token for a URL: 'u!' + unpadded base64url of the URL."""
    url = url.strip()
    if not is_onedrive_url(url):
        raise OneDriveError(f"Not a OneDrive link: {url[:60]}")
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def nfc(name: str) -> str:
    """Names compared exactly, after Unicode normalisation (Greek accents can be stored decomposed)."""
    return unicodedata.normalize("NFC", name or "").strip()


def parse_template_names(text: str) -> dict[str, str]:
    """'VS=file a.label; PG=file b.label' -> {'VS': 'file a.label', ...} (also one per line)."""
    out: dict[str, str] = {}
    for part in re.split(r"[;\n]", text or ""):
        if "=" in part:
            k, v = part.split("=", 1)
            if k.strip() and v.strip():
                out[k.strip().upper()] = nfc(v)
    return out


# ---- token storage -----------------------------------------------------------------------------
class SecretBox:
    """Fernet encryption with a key derived from VSPM_SECRET_KEY (Streamlit secrets)."""

    def __init__(self, secret: str):
        if not secret or len(secret) < 16:
            raise OneDriveError("VSPM_SECRET_KEY (at least 16 characters) must be set in the Streamlit secrets "
                                "before a Microsoft account can be connected - it encrypts the stored token.")
        from cryptography.fernet import Fernet

        key = base64.urlsafe_b64encode(hashlib.sha256(("vspm-token:" + secret).encode()).digest())
        self._f = Fernet(key)

    def seal(self, data: dict) -> str:
        return self._f.encrypt(json.dumps(data).encode("utf-8")).decode("ascii")

    def open(self, token: str) -> dict:
        from cryptography.fernet import InvalidToken

        try:
            return json.loads(self._f.decrypt(token.encode("ascii")))
        except InvalidToken as e:
            raise NotConnected("The stored Microsoft token cannot be decrypted (VSPM_SECRET_KEY changed?). "
                               "Connect the Microsoft account again.") from e


class TokenStore:
    """Encrypted OAuth tokens in the shared database (table cloud_credentials)."""

    def __init__(self, db, box_factory: Callable[[], SecretBox], provider: str = "microsoft"):
        self.db = db
        self.box_factory = box_factory
        self.provider = provider

    def load(self) -> Optional[dict]:
        row = self.db.one("SELECT secret_enc FROM cloud_credentials WHERE provider=?", (self.provider,))
        return self.box_factory().open(row["secret_enc"]) if row else None

    def save(self, tokens: dict, account: str) -> None:
        from vspm.db.database import utcnow

        enc = self.box_factory().seal(tokens)
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO cloud_credentials (provider, account, secret_enc, updated_at) VALUES (?,?,?,?) "
                         "ON CONFLICT(provider) DO UPDATE SET account=excluded.account, "
                         "secret_enc=excluded.secret_enc, updated_at=excluded.updated_at",
                         (self.provider, account, enc, utcnow()))

    def info(self) -> Optional[dict]:
        row = self.db.one("SELECT account, updated_at FROM cloud_credentials WHERE provider=?", (self.provider,))
        return {"account": row["account"], "updated_at": row["updated_at"]} if row else None

    def clear(self) -> None:
        self.db.execute("DELETE FROM cloud_credentials WHERE provider=?", (self.provider,))


# ---- sign-in -----------------------------------------------------------------------------------
@dataclass
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_at: float
    interval: int
    message: str


class MicrosoftAuth:
    def __init__(self, client_id: str, store: TokenStore, *, tenant: str = "consumers",
                 http: Optional[httpx.Client] = None, clock: Callable[[], float] = time.time):
        if not client_id:
            raise OneDriveError("VSPM_MS_CLIENT_ID is not set (Entra app registration - see DEPLOY_STREAMLIT.md).")
        self.client_id = client_id
        self.tenant = tenant
        self.store = store
        self.http = http or httpx.Client(timeout=20)
        self.clock = clock

    def _token_url(self) -> str:
        return f"{LOGIN}/{self.tenant}/oauth2/v2.0/token"

    def start_device_login(self) -> DeviceCode:
        r = self.http.post(f"{LOGIN}/{self.tenant}/oauth2/v2.0/devicecode",
                           data={"client_id": self.client_id, "scope": SCOPES})
        d = _json_or_error(r, "Microsoft sign-in could not start")
        return DeviceCode(d["device_code"], d["user_code"], d.get("verification_uri", "https://microsoft.com/devicelogin"),
                          self.clock() + int(d.get("expires_in", 900)), int(d.get("interval", 5)), d.get("message", ""))

    def finish_device_login(self, code: DeviceCode) -> str:
        """One poll. Raises AuthorizationPending until the admin has signed in. Returns the account name."""
        if self.clock() > code.expires_at:
            raise OneDriveError("The sign-in code expired. Start again.")
        r = self.http.post(self._token_url(), data={
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": self.client_id,
            "device_code": code.device_code})
        if r.status_code == 400:
            err = (r.json() or {}).get("error")
            if err in ("authorization_pending", "slow_down"):
                raise AuthorizationPending("Waiting for the sign-in at microsoft.com/devicelogin …")
            raise OneDriveError(f"Microsoft sign-in failed: {err}")
        tokens = self._tokens(_json_or_error(r, "Microsoft sign-in failed"))
        me = self._get_me(tokens["access_token"])
        account = me.get("userPrincipalName") or me.get("mail") or me.get("displayName") or "Microsoft account"
        self.store.save(tokens, account)
        return account

    def _tokens(self, d: dict) -> dict:
        if "refresh_token" not in d:
            raise OneDriveError("Microsoft did not return a refresh token (offline_access missing in the app?).")
        return {"access_token": d["access_token"], "refresh_token": d["refresh_token"],
                "expires_at": self.clock() + int(d.get("expires_in", 3600)) - 120}

    def _get_me(self, access_token: str) -> dict:
        r = self.http.get(f"{GRAPH}/me", headers={"Authorization": f"Bearer {access_token}"})
        return r.json() if r.status_code == 200 else {}

    def access_token(self) -> str:
        tokens = self.store.load()
        if not tokens:
            raise NotConnected("No Microsoft account is connected. Settings → Cloud sources → Connect Microsoft "
                               "account.")
        if tokens["expires_at"] > self.clock():
            return tokens["access_token"]
        r = self.http.post(self._token_url(), data={
            "grant_type": "refresh_token", "client_id": self.client_id, "refresh_token": tokens["refresh_token"],
            "scope": SCOPES})
        if r.status_code in (400, 401):
            raise NotConnected("The Microsoft sign-in has expired or was revoked. Connect the account again "
                               "(Settings → Cloud sources).")
        new = self._tokens(_json_or_error(r, "Microsoft token refresh failed"))
        info = self.store.info() or {}
        self.store.save(new, info.get("account", "Microsoft account"))  # refresh tokens rotate
        return new["access_token"]


def _json_or_error(r: httpx.Response, what: str) -> dict:
    if r.status_code >= 400:
        try:
            d = r.json()
            detail = d.get("error_description") or d.get("error", {}).get("message") or d.get("error")
        except Exception:
            detail = r.text[:200]
        raise OneDriveError(f"{what} (HTTP {r.status_code}): {str(detail)[:300]}")
    return r.json()


# ---- files -------------------------------------------------------------------------------------
@dataclass
class DriveItem:
    drive_id: str
    item_id: str
    name: str
    etag: str
    ctag: str = ""
    size: int = 0
    modified: str = ""
    is_folder: bool = False
    web_url: str = ""
    raw: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_graph(cls, d: dict) -> "DriveItem":
        return cls(drive_id=(d.get("parentReference") or {}).get("driveId", ""), item_id=d["id"],
                   name=nfc(d.get("name", "")), etag=d.get("eTag", ""), ctag=d.get("cTag", ""),
                   size=int(d.get("size") or 0), modified=d.get("lastModifiedDateTime", ""),
                   is_folder="folder" in d, web_url=d.get("webUrl", ""), raw=d)


class GraphFiles:
    def __init__(self, token: Callable[[], str], http: Optional[httpx.Client] = None):
        self.token = token
        self.http = http or httpx.Client(timeout=60)

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        headers = {"Authorization": f"Bearer {self.token()}", **kw.pop("headers", {})}
        for attempt in range(3):
            r = self.http.request(method, url if url.startswith("http") else GRAPH + url, headers=headers,
                                  follow_redirects=False, **kw)
            if r.status_code in (429, 503) and attempt < 2:
                time.sleep(min(float(r.headers.get("Retry-After", "2")), 10))
                continue
            break
        if r.status_code == 401:
            raise NotConnected("Microsoft rejected the access token. Connect the account again.")
        if r.status_code == 403:
            raise OneDriveError("No permission for this OneDrive item (is the connected account the owner?).")
        if r.status_code == 404:
            raise OneDriveError("OneDrive item not found (link deleted, moved, or another account's file).")
        if r.status_code == 412:
            raise PreconditionFailed("The file was changed in OneDrive since it was read.")
        if r.status_code == 423:
            raise FileLocked("The file is locked in OneDrive (open for editing).")
        if r.status_code >= 400 and r.status_code not in (301, 302, 303, 307, 308):
            raise OneDriveError(f"OneDrive error HTTP {r.status_code}: {r.text[:200]}")
        return r

    def resolve_link(self, url: str) -> DriveItem:
        r = self._req("GET", f"/shares/{share_id(url)}/driveItem")
        return DriveItem.from_graph(r.json())

    def resolve_path(self, path: str) -> DriveItem:
        """Fallback: a path in the connected account's own OneDrive, e.g. 'Documents/Labels'."""
        r = self._req("GET", f"/me/drive/root:/{path.strip().strip('/')}")
        return DriveItem.from_graph(r.json())

    def item(self, drive_id: str, item_id: str) -> DriveItem:
        return DriveItem.from_graph(self._req("GET", f"/drives/{drive_id}/items/{item_id}").json())

    def children(self, drive_id: str, item_id: str) -> list[DriveItem]:
        out: list[DriveItem] = []
        url: Optional[str] = f"/drives/{drive_id}/items/{item_id}/children?$top=200"
        while url:
            d = self._req("GET", url).json()
            out += [DriveItem.from_graph({**x, "parentReference": {**(x.get("parentReference") or {}),
                                                                    "driveId": drive_id}}) for x in d.get("value", [])]
            url = d.get("@odata.nextLink")
        return out

    def download(self, drive_id: str, item_id: str) -> tuple[bytes, DriveItem]:
        """Content and the metadata it belongs to (re-read if the file changed during the download)."""
        for _ in range(3):
            before = self.item(drive_id, item_id)
            r = self._req("GET", f"/drives/{drive_id}/items/{item_id}/content")
            if r.status_code in (301, 302, 303, 307, 308):  # pre-authenticated URL: no Authorization header
                r = self.http.get(r.headers["Location"], follow_redirects=True)
                if r.status_code >= 400:
                    raise OneDriveError(f"OneDrive download failed (HTTP {r.status_code})")
            data = r.content
            after = self.item(drive_id, item_id)
            if after.etag == before.etag:
                return data, after
        raise PreconditionFailed("The file kept changing while it was downloaded.")

    def replace_if_match(self, drive_id: str, item_id: str, data: bytes, etag: str) -> DriveItem:
        """Replace the whole file, only if its eTag is still `etag` (otherwise PreconditionFailed)."""
        if not etag:
            raise OneDriveError("Refusing to upload without an eTag (could overwrite someone else's changes).")
        r = self._req("PUT", f"/drives/{drive_id}/items/{item_id}/content", content=data,
                      headers={"If-Match": etag, "Content-Type": "application/octet-stream"})
        return DriveItem.from_graph(r.json())

    def drive_info(self) -> dict[str, Any]:
        return self._req("GET", "/me/drive").json()
