"""Collabora Online, the practice's own office suite (optional): open and edit documents in the browser, together.

Collabora is a separate, self-hosted service; the hub is its **WOPI host** (``integrations/wopi.py``). This module
holds what the hub knows about Collabora:

* **Configuration**, by environment. Without ``PRACTICEHUB_COLLABORA_URL`` there is no Collabora, and nothing on any
  page mentions it.

  - ``PRACTICEHUB_COLLABORA_URL``: Collabora as the browser reaches it (``https://office.example.ca``).
  - ``PRACTICEHUB_COLLABORA_DISCOVERY_URL``: Collabora as the hub reaches it, if that differs (``http://collabora:9980``
    inside compose). Default: the URL above.
  - ``PRACTICEHUB_WOPI_BASE``: the hub as Collabora reaches it (``http://hub:8200``). Inside a container 127.0.0.1 is
    the container, not the hub. Collabora's ``aliasgroup1`` must name this.

* **Discovery**: ``/hosting/discovery`` is fetched (stdlib ``urllib`` and ``xml.etree``) and kept for an hour; a failed
  fetch is retried after a minute, and until then the office buttons are simply not shown. It maps each file
  extension to its view and edit action URLs, and carries Collabora's proof keys.

* **Proof keys**: every WOPI call Collabora makes is signed (``X-WOPI-Proof``, ``X-WOPI-ProofOld``: RSA PKCS#1 v1.5
  over SHA-256). RSA verification is modular exponentiation, so it is done here with ``pow`` and ``hashlib``, no
  crypto library. When discovery has a proof key, a WOPI call without a valid proof is refused.

* **Blank documents**: minimal valid OpenDocument files (text, spreadsheet, presentation), made with ``zipfile``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import os
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field

# Formats the record page offers to open, when discovery has an action for them.
OFFICE_EXTENSIONS = {"odt", "ods", "odp", "odg", "docx", "xlsx", "pptx", "doc", "xls", "ppt", "rtf", "csv", "txt"}
_MIME = {
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
    "odg": "application/vnd.oasis.opendocument.graphics",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "csv": "text/csv", "txt": "text/plain",
}
_BY_MIME = {v: k for k, v in _MIME.items()}


def extension(name: str, content_type: str = "") -> str:
    """A file's office extension, from its name, else its content type; "" when it is not an office file."""
    ext = (name or "").rsplit(".", 1)[-1].lower() if "." in (name or "") else ""
    if ext in OFFICE_EXTENSIONS:
        return ext
    return _BY_MIME.get((content_type or "").split(";", 1)[0].strip().lower(), "")


def mime_of(ext: str) -> str:
    return _MIME.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------------------------------- discovery
@dataclass
class ProofKey:
    modulus: int
    exponent: int


@dataclass
class Discovery:
    actions: dict[str, dict[str, str]] = field(default_factory=dict)  # ext -> {"view"|"edit"|...: urlsrc}
    proof: ProofKey | None = None
    proof_old: ProofKey | None = None

    def action(self, ext: str, want: str = "edit") -> str | None:
        acts = self.actions.get(ext.lower()) or {}
        if want == "view":
            return acts.get("view") or acts.get("edit") or acts.get("embedview")
        return acts.get("edit")


def _rebase(urlsrc: str, public: str) -> str:
    """An action URL on the origin the browser uses: Collabora names itself as the hub reached it."""
    src, pub = urllib.parse.urlsplit(urlsrc), urllib.parse.urlsplit(public)
    out = urllib.parse.urlunsplit((pub.scheme or src.scheme, pub.netloc or src.netloc, src.path, src.query, ""))
    return out + urlsrc[len(urlsrc.rstrip("?&")):] if not out.endswith(("?", "&")) else out  # urlunsplit drops "?"


def _strip_placeholders(urlsrc: str) -> str:
    """``urlsrc`` may hold optional parameters as ``<name=PLACEHOLDER&>``; the hub fills in none of them."""
    out, depth = [], 0
    for ch in urlsrc:
        if ch == "<":
            depth += 1
        elif ch == ">" and depth:
            depth -= 1
        elif not depth:
            out.append(ch)
    s = "".join(out)
    return s if s.endswith(("?", "&")) else s + ("&" if "?" in s else "?")


def _key(el, prefix: str = "") -> ProofKey | None:
    m, e = el.get(prefix + "modulus"), el.get(prefix + "exponent")
    if not m or not e:
        return None
    try:
        return ProofKey(int.from_bytes(base64.b64decode(m), "big"), int.from_bytes(base64.b64decode(e), "big"))
    except (ValueError, TypeError):
        return None


def parse_discovery(xml: bytes, public_url: str) -> Discovery:
    root = ET.fromstring(xml)
    d = Discovery()
    for action in root.iter("action"):
        ext, name, src = (action.get("ext") or "").lower(), action.get("name") or "", action.get("urlsrc") or ""
        if ext and name and src:
            d.actions.setdefault(ext, {})[name] = _rebase(_strip_placeholders(src), public_url)
    pk = root.find("proof-key")
    if pk is not None:
        d.proof, d.proof_old = _key(pk), _key(pk, "old")
    return d


class Collabora:
    """Collabora as configured, with its discovery cached."""

    TTL, RETRY = 3600.0, 60.0

    def __init__(self, url: str, wopi_base: str, discovery_url: str | None = None, timeout: float = 3.0):
        self.url = url.rstrip("/")
        self.wopi_base = wopi_base.rstrip("/")
        self.discovery_url = (discovery_url or url).rstrip("/") + "/hosting/discovery"
        self.timeout = timeout
        self._lock = threading.Lock()
        self._cached: tuple[float, Discovery | None] | None = None

    @classmethod
    def from_env(cls) -> "Collabora | None":
        url = os.environ.get("PRACTICEHUB_COLLABORA_URL", "").strip()
        if not url:
            return None
        if not url.startswith(("http://", "https://")):
            raise SystemExit("PRACTICEHUB_COLLABORA_URL is an http(s) URL")
        base = os.environ.get("PRACTICEHUB_WOPI_BASE", "").strip() or "http://127.0.0.1:8200"
        return cls(url, base, os.environ.get("PRACTICEHUB_COLLABORA_DISCOVERY_URL", "").strip() or None)

    @property
    def origin(self) -> str:
        u = urllib.parse.urlsplit(self.url)
        return f"{u.scheme}://{u.netloc}"

    def discovery(self) -> Discovery | None:
        """Collabora's discovery, or None while it cannot be had (the office buttons are then not shown)."""
        now = time.monotonic()
        with self._lock:
            if self._cached and now - self._cached[0] < (self.TTL if self._cached[1] else self.RETRY):
                return self._cached[1]
        try:
            with urllib.request.urlopen(self.discovery_url, timeout=self.timeout) as resp:
                got = parse_discovery(resp.read(), self.url)
            got = got if got.actions else None
        except Exception:  # noqa: BLE001 - unreachable, refused, not XML: no office today, and nothing broken
            got = None
        with self._lock:
            self._cached = (now, got)
        return got

    def forget(self) -> None:
        """Fetch discovery again on next use: proof keys rotate, and a failed proof may mean they did."""
        with self._lock:
            self._cached = None

    def action(self, ext: str, want: str = "edit") -> str | None:
        d = self.discovery()
        return d.action(ext, want) if d else None


# ---------------------------------------------------------------------------------------------------- proof keys
_SHA256_INFO = bytes.fromhex("3031300d060960864801650304020105000420")  # DER DigestInfo prefix for SHA-256
_EPOCH_TICKS = 621355968000000000  # .NET ticks (100 ns since 0001-01-01) at the Unix epoch
PROOF_WINDOW = 20 * 60  # seconds: a proof older (or newer) than this is a replay


def rsa_verify(key: ProofKey, message: bytes, signature_b64: str) -> bool:
    """RSASSA-PKCS1-v1_5 with SHA-256: sig^e mod n is the padded digest of the message."""
    try:
        sig = base64.b64decode(signature_b64 or "", validate=True)
    except ValueError:
        return False
    k = (key.modulus.bit_length() + 7) // 8
    if not sig or len(sig) != k:
        return False
    s = int.from_bytes(sig, "big")
    if s >= key.modulus:
        return False
    em = pow(s, key.exponent, key.modulus).to_bytes(k, "big")
    t = _SHA256_INFO + hashlib.sha256(message).digest()
    if k < len(t) + 11:
        return False
    return hmac.compare_digest(em, b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t)


def proof_message(access_token: str, url: str, ticks: int) -> bytes:
    tok, u = access_token.encode(), url.upper().encode()
    return (len(tok).to_bytes(4, "big") + tok + len(u).to_bytes(4, "big") + u
            + (8).to_bytes(4, "big") + int(ticks).to_bytes(8, "big", signed=True))


def ticks_now() -> int:
    return _EPOCH_TICKS + int(time.time() * 10_000_000)


def check_proof(d: Discovery, access_token: str, url: str, headers: dict) -> bool:
    """A WOPI call's proof (headers lower-cased), as the WOPI spec checks it: the current proof with the current key,
    the old proof with the current key, or the current proof with the old key; and its timestamp is fresh."""
    try:
        ticks = int(headers.get("x-wopi-timestamp", ""))
    except ValueError:
        return False
    if abs((ticks - _EPOCH_TICKS) / 10_000_000 - time.time()) > PROOF_WINDOW:
        return False
    msg = proof_message(access_token, url, ticks)
    proof, old = headers.get("x-wopi-proof", ""), headers.get("x-wopi-proofold", "")
    return bool(d.proof and (rsa_verify(d.proof, msg, proof) or (old and rsa_verify(d.proof, msg, old)))
                or d.proof_old and rsa_verify(d.proof_old, msg, proof))


# ---------------------------------------------------------------------------------------------------- blank documents
_NS = ('xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
       'xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" '
       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
       'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
       'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
       'xmlns:presentation="urn:oasis:names:tc:opendocument:xmlns:presentation:1.0" '
       'xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" '
       'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
       'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" office:version="1.3"')
_BODIES = {
    "odt": "<office:text><text:p/></office:text>",
    "ods": ('<office:spreadsheet><table:table table:name="Sheet1"><table:table-column/>'
            "<table:table-row><table:table-cell/></table:table-row></table:table></office:spreadsheet>"),
    "odp": '<office:presentation><draw:page draw:name="Slide 1" draw:master-page-name="Default"/></office:presentation>',
}
_STYLES = {
    "odp": ('<office:automatic-styles><style:page-layout style:name="PM1"><style:page-layout-properties '
            'fo:page-width="28cm" fo:page-height="15.75cm" style:print-orientation="landscape"/></style:page-layout>'
            '</office:automatic-styles><office:master-styles><style:master-page style:name="Default" '
            'style:page-layout-name="PM1"/></office:master-styles>'),
}
BLANK_KINDS = {"odt": "Text document", "ods": "Spreadsheet", "odp": "Presentation"}


def blank_document(kind: str) -> bytes:
    """An empty OpenDocument file: ``odt``, ``ods`` or ``odp``. The ``mimetype`` entry comes first, uncompressed, as
    the OpenDocument package format requires."""
    if kind not in _BODIES:
        raise ValueError(f"no blank {kind!r} document")
    mime = mime_of(kind)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype", (1980, 1, 1, 0, 0, 0)), mime, compress_type=zipfile.ZIP_STORED)
        deflate = lambda name, text: z.writestr(zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)), text,
                                                compress_type=zipfile.ZIP_DEFLATED)
        head = '<?xml version="1.0" encoding="UTF-8"?>\n'
        deflate("content.xml", f"{head}<office:document-content {_NS}><office:body>{_BODIES[kind]}</office:body>"
                               f"</office:document-content>")
        deflate("styles.xml", f"{head}<office:document-styles {_NS}>{_STYLES.get(kind, '')}</office:document-styles>")
        deflate("META-INF/manifest.xml",
                f'{head}<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
                f'manifest:version="1.3"><manifest:file-entry manifest:full-path="/" manifest:version="1.3" '
                f'manifest:media-type="{mime}"/><manifest:file-entry manifest:full-path="content.xml" '
                f'manifest:media-type="text/xml"/><manifest:file-entry manifest:full-path="styles.xml" '
                f'manifest:media-type="text/xml"/></manifest:manifest>')
    return buf.getvalue()
