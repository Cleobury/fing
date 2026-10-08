"""Phone remote: a small HTTPS server on the local network serving a hold-to-talk page.

The phone records with its own microphone and uploads the clip when the button is let go; the PC then
handles it exactly as if Right Ctrl had been held. Browsers only give a page the microphone over HTTPS, so
the server uses a self-signed certificate made on first start (the phone shows a warning once).

Every API call carries the PIN shown in Settings → Phone; too many wrong PINs lock the API for a while.
"""

from __future__ import annotations

import datetime
import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import ssl
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

import numpy as np

from .settings import DATA_DIR

if TYPE_CHECKING:
    from .app import App

CERT_PATH = os.path.join(DATA_DIR, "remote-cert.pem")
KEY_PATH = os.path.join(DATA_DIR, "remote-key.pem")
PAGE_PATH = os.path.join(os.path.dirname(__file__), "web", "index.html")
DEFAULT_PORT = 8765
SAMPLE_RATE = 16000
MAX_CLIP_S = 60
_MAX_FAILURES = 10  # wrong PINs before the API locks
_LOCKOUT_S = 300

log = logging.getLogger(__name__)


def new_pin() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def lan_ip() -> str:
    """This PC's address on the local network (the interface that would reach the internet)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))  # no packet is sent; this just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def url(port: int, pin: str = "") -> str:
    """The address to open on the phone; with a PIN, the page pairs itself (the part after # isn't sent)."""
    return f"https://{lan_ip()}:{port}/" + (f"#pin={pin}" if pin else "")


def _ensure_cert(ip: str) -> None:
    """A self-signed certificate for this PC's name and LAN address, remade when the address changes."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    if os.path.exists(CERT_PATH) and os.path.exists(KEY_PATH):
        with open(CERT_PATH, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        ips = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
        if ipaddress.ip_address(ip) in ips and cert.not_valid_after_utc > datetime.datetime.now(datetime.UTC):
            return
    key = ec.generate_private_key(ec.SECP256R1())
    host = socket.gethostname()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Fing ({host})")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=825))
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName(host), x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address(ip)), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(KEY_PATH, "wb") as f:
        f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    with open(CERT_PATH, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    log.info("Made a certificate for the phone remote (%s, %s)", host, ip)


class RemoteServer:
    def __init__(self, app: App, port: int, pin: str):
        self.app, self.port, self.pin = app, port, pin
        self._failures = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()
        _ensure_cert(lan_ip())
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(CERT_PATH, KEY_PATH)
        self._httpd = ThreadingHTTPServer(("0.0.0.0", port), _make_handler(self))
        self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self._httpd.daemon_threads = True
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="phone-remote").start()
        log.info("Phone remote listening on %s", url(port))

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()

    def check_pin(self, given: str) -> HTTPStatus | None:
        """None if the PIN is right; otherwise the status to answer with."""
        with self._lock:
            if time.monotonic() < self._locked_until:
                return HTTPStatus.TOO_MANY_REQUESTS
            if hmac.compare_digest(given.encode(), self.pin.encode()):
                self._failures = 0
                return None
            self._failures += 1
            if self._failures >= _MAX_FAILURES:
                log.warning("Phone remote: too many wrong PINs; locking for %d s", _LOCKOUT_S)
                self._locked_until = time.monotonic() + _LOCKOUT_S
                self._failures = 0
            return HTTPStatus.UNAUTHORIZED


def _make_handler(server: RemoteServer):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args) -> None:
            log.debug("Phone remote: " + fmt, *args)

        def _send(self, status: HTTPStatus, body: bytes = b"", ctype: str = "application/json") -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(status, json.dumps(data).encode())

        def _authorised(self) -> bool:
            status = server.check_pin(self.headers.get("X-Jev-Pin", ""))
            if status is not None:
                self._json({"error": "locked" if status == HTTPStatus.TOO_MANY_REQUESTS else "pin"}, status)
            return status is None

        def _body(self, limit: int) -> bytes | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > limit:
                self._json({"error": "too long"}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                self.close_connection = True
                return None
            return self.rfile.read(n)

        def do_GET(self) -> None:
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                with open(PAGE_PATH, "rb") as f:
                    self._send(HTTPStatus.OK, f.read(), "text/html; charset=utf-8")
            elif path == "/api/status":
                if self._authorised():
                    self._json(server.app.remote_status())
            else:
                self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")

        def do_POST(self) -> None:
            path = self.path.split("?")[0]
            if path == "/api/release":
                # Read the clip before checking the PIN so a refused upload doesn't leave the body unread.
                body = self._body(MAX_CLIP_S * 48000 * 2)
                if body is None or not self._authorised():
                    return
                rate = int(self.headers.get("X-Sample-Rate") or SAMPLE_RATE)
                if not 8000 <= rate <= 96000:
                    self._json({"error": "bad sample rate"}, HTTPStatus.BAD_REQUEST)
                    return
                audio = np.frombuffer(body[: len(body) // 2 * 2], "<i2").astype(np.float32) / 32768
                if rate != SAMPLE_RATE and len(audio):
                    n = round(len(audio) * SAMPLE_RATE / rate)
                    audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype(np.float32)
                server.app.remote_release(audio)
                self._json(server.app.remote_status())
                return
            body = self._body(4096)
            if body is None or not self._authorised():
                return
            if path == "/api/press":
                self._json({"result": server.app.remote_press(), **server.app.remote_status()})
            elif path == "/api/stop":
                server.app.remote_stop()
                self._json(server.app.remote_status())
            elif path == "/api/answer":
                try:
                    choice = int(json.loads(body or b"{}")["option"])
                except (ValueError, KeyError, TypeError):
                    self._json({"error": "bad option"}, HTTPStatus.BAD_REQUEST)
                    return
                server.app.remote_answer(choice)
                self._json(server.app.remote_status())
            else:
                self._send(HTTPStatus.NOT_FOUND, b"", "text/plain")

    return Handler
