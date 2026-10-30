"""Test double of the local DYMO Web Service (DYMO Label v8 / DYMO Connect).

For automated browser tests only — it is never used in production. It speaks the
same HTTPS protocol the official dymo.connect.framework.js uses:

    GET  /DYMO/DLS/Printing/StatusConnected        -> true
    GET  /DYMO/DLS/Printing/GetPrinters            -> "<Printers>...</Printers>" (JSON string)
    POST /DYMO/DLS/Printing/PrintLabel             form: printerName, printParamsXml, labelXml, labelSetXml
    POST /DYMO/DLS/Printing/RenderLabel            form: labelXml, renderParamsXml, printerName -> base64 PNG
    GET  /DYMO/DLS/Printing/Is550Printer           -> false

and answers cross-origin requests with `Access-Control-Allow-Origin: *`, like the
real service must for the framework to work from any web site.

Test hooks (not part of DYMO's API):
    GET  /__test/jobs           printed jobs (printer, label XML, print params)
    POST /__test/config         JSON {"printers": [...], "fail_print": "message"|null, "status": true|false}
    POST /__test/reset

Run standalone:  python -m vspm.testing_dymo_service --port 41951
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import ssl
import tempfile
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse
from xml.sax.saxutils import escape

PATH = "/DYMO/DLS/Printing/"
TINY_PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==")


@dataclass
class FakeState:
    printers: list[dict] = field(default_factory=lambda: [
        {"name": "DYMO LabelWriter Wireless", "model": "DYMO LabelWriter Wireless", "connected": True}])
    fail_print: Optional[str] = None
    status: bool = True
    jobs: list[dict] = field(default_factory=list)
    requests: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


def printers_xml(printers: list[dict]) -> str:
    items = "".join(
        f"<LabelWriterPrinter><Name>{escape(p['name'])}</Name><ModelName>{escape(p.get('model', p['name']))}"
        f"</ModelName><IsConnected>{'True' if p.get('connected', True) else 'False'}</IsConnected>"
        f"<IsLocal>True</IsLocal><IsTwinTurbo>False</IsTwinTurbo></LabelWriterPrinter>" for p in printers)
    return f"<Printers>{items}</Printers>"


def make_handler(state: FakeState, cors: bool = True):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: str, ctype: str = "application/json") -> None:
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self):  # CORS preflight (not needed for the framework's simple requests)
            self.send_response(204)
            if cors:
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _form(self) -> dict[str, str]:
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n).decode("utf-8") if n else ""
            return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

        def do_GET(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            with state.lock:
                state.requests.append(f"GET {u.path}")
            if u.path == "/__test/jobs":
                return self._send(200, json.dumps(state.jobs, ensure_ascii=False))
            if not u.path.startswith(PATH):
                return self._send(404, '"not found"')
            cmd = u.path[len(PATH):]
            if cmd == "StatusConnected":
                # A stopped DYMO service refuses connections; the framework treats any HTTP
                # error as "not found" (and any 200 as found), so answer 503 when "down".
                return self._send(200, "true") if state.status else self._send(503, '"service unavailable"')
            if not state.status:
                return self._send(500, '"service not ready"')
            if cmd == "GetPrinters":
                return self._send(200, json.dumps(printers_xml(state.printers)))
            if cmd in ("Is550Printer", "IsTwinTurbo"):
                return self._send(200, "false")
            if cmd == "GetConsumableInfoIn550Printer":
                return self._send(200, '""')
            return self._send(404, json.dumps(f"unknown command {cmd} {q}"))

        def do_POST(self):
            u = urlparse(self.path)
            with state.lock:
                state.requests.append(f"POST {u.path}")
            if u.path == "/__test/config":
                n = int(self.headers.get("Content-Length") or 0)
                cfg = json.loads(self.rfile.read(n) or b"{}")
                with state.lock:
                    for k in ("printers", "fail_print", "status"):
                        if k in cfg:
                            setattr(state, k, cfg[k])
                return self._send(200, "true")
            if u.path == "/__test/reset":
                with state.lock:
                    state.jobs.clear()
                    state.requests.clear()
                    state.fail_print = None
                    state.status = True
                return self._send(200, "true")
            cmd = u.path[len(PATH):] if u.path.startswith(PATH) else ""
            form = self._form()
            if cmd in ("PrintLabel", "PrintLabel2"):
                names = [p["name"] for p in state.printers]
                if form.get("printerName") not in names:
                    return self._send(500, json.dumps(f"Printer '{form.get('printerName')}' not found"))
                if state.fail_print:
                    return self._send(500, json.dumps(state.fail_print))
                with state.lock:
                    state.jobs.append({"printer": form.get("printerName"), "label_xml": form.get("labelXml", ""),
                                       "print_params": form.get("printParamsXml", ""),
                                       "at": dt.datetime.now(dt.timezone.utc).isoformat()})
                return self._send(200, "true")
            if cmd == "RenderLabel":
                return self._send(200, json.dumps(TINY_PNG))
            return self._send(404, json.dumps(f"unknown command {cmd}"))

    return Handler


def self_signed_cert(directory: Path) -> tuple[Path, Path]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                        x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_p = directory / "fake_dymo.crt"
    key_p = directory / "fake_dymo.key"
    cert_p.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_p.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    return cert_p, key_p


class FakeDymoService:
    def __init__(self, port: int = 41951, host: str = "127.0.0.1", cors: bool = True):
        self.state = FakeState()
        self.port = port
        self._dir = Path(tempfile.mkdtemp(prefix="fake-dymo-"))
        cert, key = self_signed_cert(self._dir)
        self.cert_path = cert
        self.server = ThreadingHTTPServer((host, port), make_handler(self.state, cors))
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert), str(key))
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> "FakeDymoService":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *a):
        self.stop()


def main() -> None:  # pragma: no cover
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=41951)
    ap.add_argument("--no-cors", action="store_true")
    a = ap.parse_args()
    svc = FakeDymoService(a.port, cors=not a.no_cors).start()
    print(f"Fake DYMO web service on https://127.0.0.1:{a.port}{PATH} (cert {svc.cert_path})", flush=True)
    try:
        svc.thread.join()
    except KeyboardInterrupt:
        svc.stop()


if __name__ == "__main__":  # pragma: no cover
    main()
