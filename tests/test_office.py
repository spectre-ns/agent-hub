"""Office documents in Collabora Online: the hub as a WOPI host, against a stand-in Collabora (a discovery document
served from a thread), so no Collabora runs in the tests."""

import base64
import io
import json
import random
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import M, snapshot_onto
from practicehub.integrations import collabora as cool
from practicehub.integrations.wopi import EDITED_BY, Wopi
from practicehub.web.server import App, make_handler

DISCOVERY = """<?xml version="1.0" encoding="utf-8"?>
<wopi-discovery><net-zone name="external-http">
<app name="writer"><action default="true" ext="odt" name="edit" urlsrc="http://collabora.internal:9980/browser/x/cool.html?"/>
<action ext="odt" name="view" urlsrc="http://collabora.internal:9980/browser/x/cool.html?"/></app>
<app name="calc"><action ext="ods" name="edit" urlsrc="http://collabora.internal:9980/browser/x/cool.html?&lt;ui=UI_LLCC&amp;&gt;"/></app>
<app name="impress"><action ext="odp" name="edit" urlsrc="http://collabora.internal:9980/browser/x/cool.html?"/></app>
</net-zone>{proof}</wopi-discovery>"""


def _discovery_server(proof: str = ""):
    body = DISCOVERY.replace("{proof}", proof).encode()

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.path == "/hosting/discovery"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "text/xml")
            self.send_header("Content-Length", str(len(body) if ok else 0))
            self.end_headers()
            if ok:
                self.wfile.write(body)

        def log_message(self, *a):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def mock_cool():
    httpd = _discovery_server()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.fixture
def docs(hub):
    """Document control installed, and a draft document holding a blank text document."""
    def build(hub):
        hub.approve_module_code(M(hub, "dr-amara"), "dms")
        hub.install_module(M(hub, "rana"), "dms")
        for m in hub.pack.modules:
            m.module.demo(hub)
        rana = M(hub, "rana")
        att = hub.add_file(rana, "Document", cool.blank_document("odt"), "Visitors.odt", cool.mime_of("odt"))
        rec = hub.create_record(rana, "Document", {"title": "Visitors", "number": "SOP-021", "status": "draft",
                                                   "file": att})
        return f"Document/{rec['id']}", att["blob"]
    ref, blob = snapshot_onto(hub, "office:docs", build, modules=["dms"])
    return hub, ref, blob


@pytest.fixture
def wopi(docs, mock_cool):
    hub, ref, blob = docs
    return Wopi(hub, cool.Collabora(mock_cool, "http://hub.test")), hub, ref, blob


def _call(w, method, fid, token, body=b"", **headers):
    path = f"/wopi/files/{fid}" + ("/contents" if method == "POST" and "override" not in headers
                                    or headers.pop("contents", False) else "")
    if "override" in headers:
        headers["x-wopi-override"] = headers.pop("override")
    raw = f"{path}?access_token={urllib.parse.quote(token)}"
    return w.handle(method, path, {"access_token": token}, {k.replace("_", "-"): v for k, v in headers.items()},
                    body, raw)


def _open(w, hub, ref, blob, mid, write=True):
    sess = w.open(M(hub, mid), ref, "file", blob)
    token, _ = w.issue(M(hub, mid), sess["_id"], write=write, origin="http://hub.test")
    return sess["_id"], token


def _odt(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(cool.blank_document("odt"))) as src, zipfile.ZipFile(buf, "w") as out:
        for i in src.infolist():
            data = src.read(i)
            if i.filename == "content.xml":
                data = data.replace(b"<text:p/>", f"<text:p>{text}</text:p>".encode())
            out.writestr(i, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------------- tokens
def test_access_tokens_are_bound_to_one_file_and_expire(wopi):
    w, hub, ref, blob = wopi
    fid, token = _open(w, hub, ref, blob, "rana")
    claims = w.verify(token, fid)
    assert claims["m"] == "rana" and claims["w"]
    assert w.verify(token, "0" * 40) is None  # another file
    body, sig = token.split(".")
    forged = base64.urlsafe_b64encode(json.dumps({**claims, "m": "jo"}).encode()).decode().rstrip("=")
    assert w.verify(f"{forged}.{sig}", fid) is None  # tampered
    assert w.verify(token[:-2] + ("AA" if token[-2:] != "AA" else "BB"), fid) is None
    old, exp = w.issue(M(hub, "rana"), fid, write=True, origin="", ttl=-1)
    assert w.verify(old, fid) is None and exp < time.time() * 1000  # expired; the ttl is in ms
    _, exp = w.issue(M(hub, "rana"), fid, write=True, origin="")
    assert 9.9 * 3600e3 < exp - time.time() * 1000 <= 10 * 3600e3
    assert _call(w, "GET", fid, old)[0] == 401
    assert w.file_id(ref, "file", blob) == fid and len(fid) == 40 and blob not in fid  # opaque


# ---------------------------------------------------------------------------------------------------- CheckFileInfo
def test_check_file_info_for_a_writer_a_reader_and_a_locked_record(wopi):
    w, hub, ref, blob = wopi
    fid, token = _open(w, hub, ref, blob, "rana")
    code, _, body = _call(w, "GET", fid, token)
    info = json.loads(body)
    assert code == 200 and info["UserCanWrite"] is True and info["Version"] == blob
    assert info["BaseFileName"] == "Visitors.odt" and info["Size"] > 0 and info["UserId"] == "rana"
    assert info["UserFriendlyName"] == "Rana Haddad" and "@" not in json.dumps(info)  # a name, never an email
    assert info["PostMessageOrigin"] == "http://hub.test" and info["HidePrintOption"] is False
    assert info["OwnerId"] and info["LastModifiedTime"]

    fid, jo = _open(w, hub, ref, blob, "jo")  # front desk reads documents, does not write them
    assert json.loads(_call(w, "GET", fid, jo)[2])["UserCanWrite"] is False
    assert _call(w, "POST", fid, jo, b"x", x_cool_wopi_isexitsave="true")[0] == 403

    rana = M(hub, "rana")
    installer = {"id": "module:dms", "kind": "automation", "name": "Document control", "status": "active"}
    att = hub.add_file(rana, "Document", cool.blank_document("odt"), "Released.odt", cool.mime_of("odt"))
    rel = hub.create_record(installer, "Document", {"title": "Released", "status": "approved", "file": att})
    fid, token = _open(w, hub, f"Document/{rel['id']}", att["blob"], "rana")
    assert json.loads(_call(w, "GET", fid, token)[2])["UserCanWrite"] is False  # locked: read-only
    assert _call(w, "POST", fid, token, b"x", x_cool_wopi_isexitsave="true")[0] == 403


def test_get_file_needs_read_access(wopi):
    w, hub, ref, blob = wopi
    fid, token = _open(w, hub, ref, blob, "rana")
    code, _, data = _call(w, "GET", fid, token, contents=True)
    assert code == 200 and data == cool.blank_document("odt")
    assert not hub.may_read(M(hub, "billing-agent"), "Document")
    sneaky, _ = w.issue(M(hub, "billing-agent"), fid, write=False, origin="")
    assert _call(w, "GET", fid, sneaky, contents=True)[0] == 403
    assert _call(w, "GET", fid, "nonsense", contents=True)[0] == 401


# ---------------------------------------------------------------------------------------------------- saving
def _versions(hub, ref):
    return hub.record_history(M(hub, "rana"), ref)


def test_an_autosave_is_a_working_copy_and_an_exit_save_a_version_naming_the_editors(wopi):
    w, hub, ref, blob = wopi
    before = len(_versions(hub, ref))
    fid, rana = _open(w, hub, ref, blob, "rana")
    _, amara = _open(w, hub, ref, blob, "dr-amara")  # the second person joins the same session
    assert _ == fid
    stamp = json.loads(_call(w, "GET", fid, rana)[2])["LastModifiedTime"]
    code, _, out = _call(w, "POST", fid, rana, _odt("one"), x_cool_wopi_isautosave="true",
                         x_cool_wopi_timestamp=stamp)
    assert code == 200 and len(_versions(hub, ref)) == before  # no version for an autosave
    assert hub.read_record(M(hub, "rana"), ref)["file"]["blob"] == blob
    assert _call(w, "GET", fid, amara, contents=True)[2] == _odt("one")  # but Collabora reads the working copy
    stamp = json.loads(out)["LastModifiedTime"]
    code, _, _ = _call(w, "POST", fid, amara, _odt("two"), x_cool_wopi_isautosave="false",
                       x_cool_wopi_isexitsave="true", x_cool_wopi_timestamp=stamp)
    assert code == 200
    history = _versions(hub, ref)
    assert len(history) == before + 1
    assert history[0]["comment"] == EDITED_BY + "Rana Haddad, Dr. Amara Okafor"
    rec = hub.read_record(M(hub, "rana"), ref)
    assert rec["file"]["blob"] != blob and rec["file"]["name"] == "Visitors.odt"
    assert hub.read_file(M(hub, "rana"), ref, rec["file"]["blob"])[0] == _odt("two")


def test_a_stale_timestamp_is_a_conflict(wopi):
    w, hub, ref, blob = wopi
    fid, token = _open(w, hub, ref, blob, "rana")
    code, _, body = _call(w, "POST", fid, token, _odt("x"), x_cool_wopi_timestamp="2001-01-01T00:00:00.000Z")
    assert code == 409 and json.loads(body)["COOLStatusCode"] == 1010
    assert hub.read_record(M(hub, "rana"), ref)["file"]["blob"] == blob


def test_locks(wopi):
    w, hub, ref, blob = wopi
    fid, token = _open(w, hub, ref, blob, "rana")
    assert _call(w, "POST", fid, token, override="LOCK", x_wopi_lock="L1")[0] == 200
    assert _call(w, "POST", fid, token, override="LOCK", x_wopi_lock="L2")[0] == 409
    assert _call(w, "POST", fid, token, override="GET_LOCK")[1]["X-WOPI-Lock"] == "L1"
    assert _call(w, "POST", fid, token, _odt("y"), x_wopi_lock="L2")[0] == 409
    assert _call(w, "POST", fid, token, override="REFRESH_LOCK", x_wopi_lock="L1")[0] == 200
    assert _call(w, "POST", fid, token, override="UNLOCK", x_wopi_lock="L1")[0] == 200
    assert _call(w, "POST", fid, token, override="GET_LOCK")[1]["X-WOPI-Lock"] == ""


# ---------------------------------------------------------------------------------------------------- proof keys
def _prime(bits):
    while True:
        n = random.getrandbits(bits) | (1 << bits - 1) | 1
        if all(pow(a, n - 1, n) == 1 for a in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37)):
            return n


def test_proof_keys_are_checked_with_the_standard_library():
    e = 65537
    while True:
        p, q = _prime(512), _prime(512)
        phi = (p - 1) * (q - 1)
        if p != q and phi % e:
            break
    n, d = p * q, pow(e, -1, phi)
    k = (n.bit_length() + 7) // 8

    def sign(msg):
        t = cool._SHA256_INFO + __import__("hashlib").sha256(msg).digest()
        em = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
        return base64.b64encode(pow(int.from_bytes(em, "big"), d, n).to_bytes(k, "big")).decode()

    b64 = lambda i: base64.b64encode(i.to_bytes((i.bit_length() + 7) // 8, "big")).decode()
    xml = DISCOVERY.replace("{proof}", f'<proof-key value="x" modulus="{b64(n)}" exponent="{b64(e)}"/>').encode()
    disc = cool.parse_discovery(xml, "http://127.0.0.1:9981")
    assert disc.proof.modulus == n
    assert disc.action("odt") == "http://127.0.0.1:9981/browser/x/cool.html?"
    assert disc.action("ods") == "http://127.0.0.1:9981/browser/x/cool.html?"  # placeholders dropped
    url, tok, ticks = "http://hub/wopi/files/abc?access_token=t", "t", cool.ticks_now()
    good = {"x-wopi-timestamp": str(ticks), "x-wopi-proof": sign(cool.proof_message(tok, url, ticks))}
    assert cool.check_proof(disc, tok, url, good)
    assert not cool.check_proof(disc, "other", url, good)
    assert not cool.check_proof(disc, tok, url + "x", good)
    stale = ticks - 3600 * 10_000_000
    assert not cool.check_proof(disc, tok, url, {"x-wopi-timestamp": str(stale),
                                                 "x-wopi-proof": sign(cool.proof_message(tok, url, stale))})


# ---------------------------------------------------------------------------------------------------- pages
def _page(app, path, mid="rana", method="GET", body=b""):
    return app.handle(method, path, {"cookie": f"ph_as={mid}", "host": "127.0.0.1:8204",
                                     "origin": "http://127.0.0.1:8204",
                                     "content-type": "application/x-www-form-urlencoded"}, body)


def test_office_buttons_only_when_collabora_is_set_up(docs, mock_cool, monkeypatch):
    hub, ref, blob = docs
    path = "/records/" + ref
    monkeypatch.delenv("PRACTICEHUB_COLLABORA_URL", raising=False)
    page = _page(App(hub, dev=True), path)[2].decode()
    assert "Visitors.odt" in page and "Edit together" not in page and "New document" not in page
    assert _page(App(hub, dev=True), f"/wopi/files/{'0' * 40}?access_token=x")[0] == 404

    monkeypatch.setenv("PRACTICEHUB_COLLABORA_URL", "http://127.0.0.1:9")  # set, but unreachable
    assert "Edit together" not in _page(App(hub, dev=True), path)[2].decode()

    monkeypatch.setenv("PRACTICEHUB_COLLABORA_URL", mock_cool)
    monkeypatch.setenv("PRACTICEHUB_WOPI_BASE", "http://172.17.0.1:8204")
    app = App(hub, dev=True)
    page = _page(app, path)[2].decode()
    assert "Edit together" in page and ">Open<" in page
    assert "Edit together" not in _page(app, path, "jo")[2].decode()  # a reader only opens it
    code, headers, editor = _page(app, f"{path}/office/file/{blob}")
    editor = editor.decode()
    assert code == 200 and f"frame-src {mock_cool}" in headers["Content-Security-Policy"]
    assert 'name="access_token"' in editor and 'name="access_token_ttl"' in editor
    assert "WOPISrc=http%3A%2F%2F172.17.0.1%3A8204%2Fwopi%2Ffiles%2F" in editor
    assert "permission=readonly" in _page(app, f"{path}/office/file/{blob}?view=1")[2].decode()


def test_a_new_blank_document(docs, mock_cool, monkeypatch):
    hub, _, _ = docs
    monkeypatch.setenv("PRACTICEHUB_COLLABORA_URL", mock_cool)
    app = App(hub, dev=True)
    rec = hub.create_record(M(hub, "rana"), "Document", {"title": "Rota", "status": "draft"})
    path = f"/records/Document/{rec['id']}"
    assert "New document" in _page(app, path)[2].decode()
    code, headers, _ = _page(app, f"{path}/office/file/new", method="POST", body=b"kind=ods&name=Rota")
    assert code == 303 and "/office/file/" in headers["Location"]
    att = hub.read_record(M(hub, "rana"), f"Document/{rec['id']}")["file"]
    assert att["name"] == "Rota.ods" and att["contentType"] == cool.mime_of("ods")
    data = hub.read_file(M(hub, "rana"), f"Document/{rec['id']}", att["blob"])[0]
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist()[0] == "mimetype" and z.read("mimetype") == cool.mime_of("ods").encode()
    assert z.infolist()[0].compress_type == zipfile.ZIP_STORED
    for kind in cool.BLANK_KINDS:
        assert b"office:document-content" in zipfile.ZipFile(io.BytesIO(cool.blank_document(kind))).read("content.xml")


def test_wopi_listener_serves_nothing_else(docs):
    hub, _, _ = docs
    handler = make_handler(App(hub, dev=True), only="/wopi/")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        import urllib.request
        import urllib.error
        for path in ("/login?as=rana", "/records"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{httpd.server_port}{path}")
            assert exc.value.code == 404
    finally:
        httpd.shutdown()


def test_a_signed_file_saved_again_is_said_to_be_unsigned(docs, mock_cool, monkeypatch):
    hub, _, _ = docs
    monkeypatch.setenv("PRACTICEHUB_COLLABORA_URL", mock_cool)
    app = App(hub, dev=True)
    installer = {"id": "module:dms", "kind": "automation", "name": "Document control", "status": "active"}
    att = hub.add_file(M(hub, "rana"), "Document", cool.blank_document("odt"), "Signed.odt", cool.mime_of("odt"))
    ref, blob = f"Document/{hub.create_record(installer, 'Document', {'title': 'S', 'status': 'approved', 'file': att})['id']}", att["blob"]
    hub.sign_record(M(hub, "dr-amara"), ref, "I have read and understood this document")
    assert "Revise" in _page(app, f"/records/{ref}/office/file/{blob}")[2].decode()  # released: read-only, says why
    hub.update_record(M(hub, "dr-amara"), ref, {"status": "draft"})  # Revise
    assert "Saving a change makes a new version, which is not signed" in _page(app, f"/records/{ref}/office/file/{blob}")[2].decode()
    fid, token = _open(app.wopi, hub, ref, blob, "rana")
    assert _call(app.wopi, "POST", fid, token, _odt("changed"), x_cool_wopi_isexitsave="true")[0] == 200
    assert "this version is not signed" in _page(app, f"/records/{ref}")[2].decode()
