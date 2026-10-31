"""Test double of Microsoft identity (device code flow) and Microsoft Graph file APIs.

Implements only what vspm.integrations.onedrive uses, with the behaviour that matters for
safety: every Graph call needs a valid bearer token, sharing links resolve only with a token,
downloads redirect to a pre-authenticated URL (which must NOT receive the token), and
`PUT /content` honours `If-Match` (HTTP 412 when the eTag is stale). Used with httpx.MockTransport.
"""

from __future__ import annotations

import base64
import json
import threading
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import unquote, urlparse

import httpx

DRIVE = "fakedrive0001"


@dataclass
class Item:
    id: str
    name: str
    parent: Optional[str]
    folder: bool = False
    content: bytes = b""
    version: int = 1
    modified: str = "2026-10-01T08:00:00Z"

    @property
    def etag(self) -> str:
        return f'"{{{self.id.upper()}}},{self.version}"'


@dataclass
class FakeMicrosoft:
    account: str = "owner@outlook.com"
    items: dict = field(default_factory=dict)
    shares: dict = field(default_factory=dict)  # share id -> item id
    approved: bool = False
    access_tokens: dict = field(default_factory=dict)  # token -> expired?
    refresh_tokens: set = field(default_factory=set)
    requests: list = field(default_factory=list)
    ignore_if_match: bool = False  # simulate a server that would NOT protect against overwrites
    before_put: Optional[Callable[["FakeMicrosoft", Item], None]] = None
    fail_put_status: Optional[int] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        self.items["root"] = Item("root", "root", None, folder=True)

    # ---- set-up helpers ----------------------------------------------------------------------
    def add_folder(self, name: str, parent: str = "root", link: Optional[str] = None) -> Item:
        it = Item(uuid.uuid4().hex[:16], name, parent, folder=True)
        self.items[it.id] = it
        if link:
            self.share(link, it.id)
        return it

    def add_file(self, name: str, content: bytes, parent: str = "root", link: Optional[str] = None) -> Item:
        it = Item(uuid.uuid4().hex[:16], name, parent, content=content)
        self.items[it.id] = it
        if link:
            self.share(link, it.id)
        return it

    def share(self, link: str, item_id: str) -> None:
        self.shares["u!" + base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")] = item_id

    def edit(self, item: Item, content: bytes) -> None:
        """Someone else saves the file (new version, new eTag)."""
        item.content = content
        item.version += 1

    def expire_tokens(self) -> None:
        for t in self.access_tokens:
            self.access_tokens[t] = True

    def puts(self, item_id: Optional[str] = None) -> list:
        return [r for r in self.requests if r[0] == "PUT" and (item_id is None or item_id in r[1])]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handle))

    # ---- protocol ----------------------------------------------------------------------------
    def _json(self, item: Item) -> dict:
        d = {"id": item.id, "name": item.name, "eTag": item.etag, "cTag": item.etag, "size": len(item.content),
             "lastModifiedDateTime": item.modified, "parentReference": {"driveId": DRIVE, "id": item.parent},
             "webUrl": f"https://onedrive.live.com/?id={item.id}"}
        d["folder" if item.folder else "file"] = {"childCount": 0} if item.folder else {"mimeType": "x"}
        return d

    def _new_tokens(self) -> dict:
        at, rt = "AT-" + uuid.uuid4().hex, "RT-" + uuid.uuid4().hex
        self.access_tokens[at] = False
        self.refresh_tokens.add(rt)
        return {"token_type": "Bearer", "access_token": at, "refresh_token": rt, "expires_in": 3600,
                "scope": "Files.ReadWrite User.Read"}

    def handle(self, req: httpx.Request) -> httpx.Response:
        with self.lock:
            self.requests.append((req.method, str(req.url), dict(req.headers)))
            u = urlparse(str(req.url))
            if u.hostname == "login.microsoftonline.com":
                return self._login(req, u.path)
            if u.hostname == "download.fake":
                if "authorization" in req.headers:
                    return httpx.Response(400, text="token sent to the download host")
                return httpx.Response(200, content=self.items[u.path.strip("/")].content)
            auth = req.headers.get("authorization", "")
            token = auth.removeprefix("Bearer ")
            if token not in self.access_tokens or self.access_tokens[token]:
                return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
            return self._graph(req, unquote(u.path.removeprefix("/v1.0")))

    def _login(self, req: httpx.Request, path: str) -> httpx.Response:
        form = dict(x.split("=", 1) for x in req.content.decode().split("&")) if req.content else {}
        form = {k: unquote(v.replace("+", " ")) for k, v in form.items()}
        if path.endswith("/devicecode"):
            assert "/consumers/" in path
            return httpx.Response(200, json={"device_code": "DC-1", "user_code": "ABCD-1234",
                                             "verification_uri": "https://microsoft.com/devicelogin",
                                             "expires_in": 900, "interval": 5,
                                             "message": "To sign in, use a web browser to open the page"})
        grant = form.get("grant_type", "")
        if grant.endswith("device_code"):
            if not self.approved:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json=self._new_tokens())
        if grant == "refresh_token":
            rt = form.get("refresh_token")
            if rt not in self.refresh_tokens:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.refresh_tokens.discard(rt)  # refresh tokens rotate
            return httpx.Response(200, json=self._new_tokens())
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _graph(self, req: httpx.Request, path: str) -> httpx.Response:
        if path == "/me":
            return httpx.Response(200, json={"userPrincipalName": self.account, "displayName": "Owner"})
        if path == "/me/drive":
            return httpx.Response(200, json={"id": DRIVE, "driveType": "personal"})
        if path.startswith("/shares/"):
            sid = path.split("/")[2]
            if sid not in self.shares:
                return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
            return httpx.Response(200, json=self._json(self.items[self.shares[sid]]))
        if path.startswith("/me/drive/root:/"):
            parts = [p for p in path.removeprefix("/me/drive/root:/").split("/") if p]
            cur = "root"
            for p in parts:
                nxt = next((i for i in self.items.values() if i.parent == cur and i.name == p), None)
                if nxt is None:
                    return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
                cur = nxt.id
            return httpx.Response(200, json=self._json(self.items[cur]))
        segs = path.split("/")  # ['', 'drives', d, 'items', i, (children|content)]
        if len(segs) >= 5 and segs[1] == "drives" and segs[3] == "items":
            item = self.items.get(segs[4])
            if item is None:
                return httpx.Response(404, json={"error": {"code": "itemNotFound"}})
            tail = segs[5] if len(segs) > 5 else ""
            if tail == "children":
                kids = [self._json(i) for i in self.items.values() if i.parent == item.id]
                return httpx.Response(200, json={"value": kids})
            if tail == "content" and req.method == "GET":
                return httpx.Response(302, headers={"Location": f"https://download.fake/{item.id}"})
            if tail == "content" and req.method == "PUT":
                if self.before_put:
                    hook, self.before_put = self.before_put, None
                    hook(self, item)
                if self.fail_put_status:
                    return httpx.Response(self.fail_put_status, json={"error": {"code": "fail"}})
                if not self.ignore_if_match and req.headers.get("if-match") != item.etag:
                    return httpx.Response(412, json={"error": {"code": "preconditionFailed"}})
                item.content = req.content
                item.version += 1
                return httpx.Response(200, json=self._json(item))
            return httpx.Response(200, json=self._json(item))
        return httpx.Response(400, json={"error": {"code": "invalidRequest", "message": path}})


def encode(obj) -> str:
    return json.dumps(obj)
