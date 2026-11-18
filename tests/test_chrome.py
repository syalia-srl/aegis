"""Pairing the person's Chrome (chrome.py): a code from Settings, a device
token that opens only the extension's socket, presence on the `chrome`
channel, and unpairing from either side."""

import io
import json
import stat
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis.app import App
from aegis.chrome import Chrome, PairError
from aegis.roots import make_roots
from aegis.web import PROTO, build_web

TOKEN = "t0ken"
PAGE = {"origin": "http://testserver"}
EXT = {"origin": "chrome-extension://abcdefghijklmnop"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: opus\n"
        "agents:\n"
        "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    return tmp_path


@pytest.fixture
def client(project, fake_claude):
    app = App(make_roots(project, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        yield c


class Page:
    """The aegis page's socket, to call operations and read the channel."""

    def __init__(self, ws):
        self.ws, self.n = ws, 0
        ws.send_json({"t": "hello", "token": TOKEN, "proto": PROTO})
        assert ws.receive_json()["t"] == "welcome"

    def call(self, op, **params):
        self.n += 1
        self.ws.send_json({"t": "call", "id": self.n, "op": op, "params": params})
        return self.until(lambda m: m["t"] == "reply" and m["id"] == self.n)

    def until(self, pred, limit=400):
        for _ in range(limit):
            m = self.ws.receive_json()
            if pred(m):
                return m
        raise AssertionError("no matching message")


def pair(c: TestClient, page: Page, **extra) -> dict:
    code = page.call("chrome.code")["result"]["code"]
    r = c.post(
        "/chrome/pair",
        json={"code": code, "name": "Chrome on Linux", **extra},
        headers=EXT,
    )
    assert r.status_code == 200, r.text
    assert r.headers["access-control-allow-origin"] == EXT["origin"]
    return r.json()


def test_a_code_pairs_once_and_the_token_opens_the_socket(client):
    with client.websocket_connect("/ws", headers=PAGE) as ws:
        page = Page(ws)
        got = pair(client, page)
        assert got["token"].startswith("aegc_") and got["server"]
        # The same code again is refused: it was one-time.
        with client.websocket_connect("/chrome/socket", headers=EXT) as ext:
            ext.send_json({"t": "hello", "token": got["token"], "version": "0.1.0"})
            welcome = ext.receive_json()
            assert welcome["t"] == "welcome" and welcome["id"] == got["id"]
            ext.send_json({"t": "ping"})
            assert ext.receive_json() == {"t": "pong"}
            page.call("chrome.theme", vars={"--accent": "#123456"})
            assert ext.receive_json() == {"t": "theme", "vars": {"--accent": "#123456"}}


def test_a_used_or_wrong_code_is_refused(client):
    with client.websocket_connect("/ws", headers=PAGE) as ws:
        page = Page(ws)
        code = page.call("chrome.code")["result"]["code"]
        assert (
            client.post(
                "/chrome/pair",
                json={"code": code.lower().replace("-", " ")},
                headers=EXT,
            ).status_code
            == 200
        )
        again = client.post("/chrome/pair", json={"code": code}, headers=EXT)
        assert again.status_code == 403 and again.json()["error"] == "bad_code"
        assert (
            client.post(
                "/chrome/pair", json={"code": "AAAA-AAAA"}, headers=EXT
            ).status_code
            == 403
        )


def test_the_preflight_names_the_extension_origin(client):
    r = client.options(
        "/chrome/pair", headers={**EXT, "access-control-request-method": "POST"}
    )
    assert (
        r.status_code == 204
        and r.headers["access-control-allow-origin"] == EXT["origin"]
    )


def test_the_socket_refuses_a_page_a_bad_token_and_the_aegis_token(client):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect("/chrome/socket", headers=PAGE) as ext:
            ext.receive_json()
    assert e.value.code == 4403
    for given in ("aegc_nope", TOKEN):
        with client.websocket_connect("/chrome/socket", headers=EXT) as ext:
            ext.send_json({"t": "hello", "token": given})
            with pytest.raises(WebSocketDisconnect) as e:
                ext.receive_json()
            assert e.value.code == 4401


def test_the_device_token_does_not_open_the_page_socket(client):
    with client.websocket_connect("/ws", headers=PAGE) as ws:
        got = pair(client, Page(ws))
    with client.websocket_connect("/ws", headers=PAGE) as ws:
        ws.send_json({"t": "hello", "token": got["token"], "proto": PROTO})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_presence_shows_on_the_channel_and_unpair_closes_the_socket(client):
    with client.websocket_connect("/ws", headers=PAGE) as ws:
        page = Page(ws)
        page.ws.send_json({"t": "sub", "channel": "chrome"})
        got = pair(client, page)
        with client.websocket_connect("/chrome/socket", headers=EXT) as ext:
            ext.send_json({"t": "hello", "token": got["token"], "version": "0.1.0"})
            ext.receive_json()

            def connected(m):
                if m.get("channel") != "chrome":
                    return False
                rows = (
                    m.get("snapshot") or (m.get("ops") or [{}])[-1].get("set") or {}
                ).get("devices", [])
                return any(d["id"] == got["id"] and d["connected"] for d in rows)

            page.until(connected)
            page.call("chrome.unpair", id=got["id"])
            assert ext.receive_json() == {"t": "unpaired"}
            with pytest.raises(WebSocketDisconnect) as e:
                ext.receive_json()
            assert e.value.code == 4401
        # The token no longer opens anything.
        with client.websocket_connect("/chrome/socket", headers=EXT) as ext:
            ext.send_json({"t": "hello", "token": got["token"]})
            with pytest.raises(WebSocketDisconnect):
                ext.receive_json()


def test_only_the_token_hash_is_kept_and_the_file_is_private(tmp_path):
    chrome = Chrome(tmp_path, lambda *_: None)
    token, dev = chrome.pair(chrome.new_code()["code"], "Brave on Linux", "Brave")
    raw = (tmp_path / "chrome.json").read_text()
    assert token not in raw and dev["token_sha"] in raw
    assert stat.S_IMODE((tmp_path / "chrome.json").stat().st_mode) == 0o600
    # A restart still knows the device.
    assert Chrome(tmp_path, lambda *_: None).device_for(token)["id"] == dev["id"]


def test_a_code_expires_after_ten_minutes(tmp_path):
    now = [1000.0]
    chrome = Chrome(tmp_path, lambda *_: None, clock=lambda: now[0])
    code = chrome.new_code()["code"]
    now[0] += 601
    with pytest.raises(PairError):
        chrome.pair(code, "x")


def test_the_extension_zip_carries_the_mark_the_fonts_and_a_valid_manifest(client):
    r = client.get("/chrome/extension.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    for need in (
        "manifest.json",
        "background.js",
        "popup.html",
        "popup.js",
        "aegis/js/glyphs.js",
        "aegis/js/mark.js",
        "aegis/css/gorgon.css",
        "aegis/fonts/inter-400.woff2",
    ):
        assert f"aegis-chrome/{need}" in names
    manifest = json.loads(z.read("aegis-chrome/manifest.json"))
    assert manifest["manifest_version"] == 3
    for rel in manifest["icons"].values():
        assert f"aegis-chrome/{rel}" in names
    assert b"@keyframes gorgon-blink" in z.read("aegis-chrome/aegis/css/gorgon.css")
