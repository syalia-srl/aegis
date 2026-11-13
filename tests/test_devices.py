"""Devices (#1060): the store, the signature check, the /device socket, the
operations agents call, and notifications to a phone."""

import asyncio
import os
import stat

import pytest

from aegis.devices import Devices
from aegis.ops import OpError

from .fake_device import Phone


def mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# -- the store ----------------------------------------------------------------


def test_pairing_one_key_twice_is_one_device(tmp_path):
    d = Devices(tmp_path / "devices.json")
    phone = Phone()
    a = d.pair("ana", "Pixel", phone.public_key)
    b = d.pair("ana", "Pixel 8", phone.public_key)
    assert a["id"] == b["id"]
    assert [r["name"] for r in d.of_owner("ana")] == ["Pixel 8"]
    assert "public_key" not in a


def test_two_phones_with_one_name_get_two_names(tmp_path):
    d = Devices(tmp_path / "devices.json")
    d.pair("ana", "Pixel", Phone().public_key)
    d.pair("ana", "Pixel", Phone().public_key)
    assert sorted(r["name"] for r in d.of_owner("ana")) == ["Pixel", "Pixel-2"]


def test_the_file_is_private_and_survives_a_reload(tmp_path):
    path = tmp_path / "devices.json"
    row = Devices(path).pair("ana", "Pixel", Phone().public_key)
    assert mode(path) == 0o600
    assert Devices(path).get(row["id"])["owner"] == "ana"


@pytest.mark.parametrize("key", ["", "not base64 !!", "AAAA"])
def test_a_bad_key_is_refused(tmp_path, key):
    with pytest.raises(ValueError):
        Devices(tmp_path / "devices.json").pair("ana", "Pixel", key)


def test_only_the_paired_key_verifies(tmp_path):
    d = Devices(tmp_path / "devices.json")
    phone, other = Phone(), Phone()
    id = d.pair("ana", "Pixel", phone.public_key)["id"]
    nonce = os.urandom(32)
    assert d.verify(id, nonce, phone.sign(nonce))
    assert not d.verify(id, nonce, other.sign(nonce))
    assert not d.verify(id, os.urandom(32), phone.sign(nonce))
    assert not d.verify("d_missing", nonce, phone.sign(nonce))
    assert not d.verify(id, nonce, "garbage")


def test_removing_an_owner_removes_only_their_devices(tmp_path):
    d = Devices(tmp_path / "devices.json")
    d.pair("ana", "Pixel", Phone().public_key)
    d.pair("ben", "Moto", Phone().public_key)
    d.remove_owner("ana")
    assert d.of_owner("ana") == []
    assert len(d.of_owner("ben")) == 1


# -- calls, without a socket ------------------------------------------------------


async def test_a_call_waits_for_its_reply(tmp_path):
    d = Devices(tmp_path / "devices.json")
    id = d.pair("ana", "Pixel", Phone().public_key)["id"]
    sent = []

    async def send(frame):
        sent.append(frame)
        d.on_reply(live, {"t": "reply", "id": frame["id"], "result": {"ok": 1}})

    live = d.attach(id, {"ops": ["device.info"], "shell": 1}, send)
    assert await d.call(id, "s", "device.info", {}, 1) == {"ok": 1}
    assert sent[0]["op"] == "device.info"
    assert d.of_owner("ana")[0]["online"] is True


async def test_a_phone_error_is_an_op_error(tmp_path):
    d = Devices(tmp_path / "devices.json")
    id = d.pair("ana", "Pixel", Phone().public_key)["id"]

    async def send(frame):
        d.on_reply(
            live, {"id": frame["id"], "error": {"code": "denied", "message": "no"}}
        )

    live = d.attach(id, {}, send)
    with pytest.raises(OpError) as e:
        await d.call(id, "s", "device.info", {}, 1)
    assert e.value.code == "denied"


async def test_a_disconnect_fails_the_call_in_flight(tmp_path):
    d = Devices(tmp_path / "devices.json")
    id = d.pair("ana", "Pixel", Phone().public_key)["id"]

    async def send(frame):
        pass

    live = d.attach(id, {}, send)
    task = asyncio.create_task(d.call(id, "s", "device.info", {}, 5))
    await asyncio.sleep(0.01)
    d.detach(id, live)
    with pytest.raises(OpError) as e:
        await task
    assert e.value.code == "device_offline"


async def test_no_answer_is_a_timeout_and_offline_is_said_at_once(tmp_path):
    d = Devices(tmp_path / "devices.json")
    id = d.pair("ana", "Pixel", Phone().public_key)["id"]
    with pytest.raises(OpError) as e:
        await d.call(id, "s", "device.info", {}, 1)
    assert e.value.code == "device_offline"

    async def send(frame):
        pass

    d.attach(id, {}, send)
    with pytest.raises(OpError) as e:
        await d.call(id, "s", "device.info", {}, 0.05)
    assert e.value.code == "device_timeout"


# -- over HTTP and the sockets ------------------------------------------------------

import threading  # noqa: E402

from starlette.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

from aegis.app import App  # noqa: E402
from aegis.roots import make_roots  # noqa: E402
from aegis.web import build_web  # noqa: E402

from .test_web import ORIGIN, TOKEN, Conn, project  # noqa: E402,F401


@pytest.fixture
def web(project, fake_claude):  # noqa: F811
    app = App(make_roots(project, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        yield app, c


def pair(c, phone, name="Pixel", signed_in=True):
    if signed_in:
        c.get(f"/?token={TOKEN}")
    r = c.post("/api/devices/pair", json={"name": name, "public_key": phone.public_key})
    if r.status_code == 200:
        phone.id = r.json()["id"]
    return r


def test_pairing_needs_the_cookie_and_json(web):
    app, c = web
    phone = Phone()
    assert pair(c, phone, signed_in=False).status_code == 401
    c.get(f"/?token={TOKEN}")
    r = c.post("/api/devices/pair", data={"public_key": phone.public_key})
    assert r.status_code == 415
    r = pair(c, phone)
    assert r.status_code == 200 and r.json()["name"] == "Pixel"
    assert app.devices.get(phone.id)["owner"] == app.who(app_caller()).name


def dcall(conn, **params):
    """device.call over /ws: Conn.call takes the operation as ``op`` itself."""
    conn.n += 1
    conn.ws.send_json(
        {"t": "call", "id": conn.n, "op": "device.call", "params": params}
    )
    while True:
        msg = conn.ws.receive_json()
        if msg.get("t") == "reply" and msg.get("id") == conn.n:
            return msg


def app_caller():
    from aegis.ops import Caller

    return Caller("user")


def test_a_wrong_signature_is_refused(web):
    _, c = web
    phone = Phone()
    pair(c, phone)
    with c.websocket_connect("/device") as ws:
        challenge = ws.receive_json()
        ws.send_json(Phone().hello(challenge) | {"id": phone.id})
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_json()
        assert e.value.code == 4401


def test_a_phone_answers_a_call_made_through_the_server(web):
    app, c = web
    phone = Phone()
    pair(c, phone)
    with c.websocket_connect("/device") as dev:
        dev.send_json(
            phone.hello(dev.receive_json(), ops=["device.info", "compass.read"])
        )
        welcome = dev.receive_json()
        assert welcome["t"] == "welcome" and welcome["name"] == "Pixel"

        def answer():
            call = dev.receive_json()
            assert call["t"] == "call" and call["op"] == "compass.read"
            dev.send_json(
                {"t": "reply", "id": call["id"], "result": {"azimuth": [212.0]}}
            )

        t = threading.Thread(target=answer)
        t.start()
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            conn = Conn(ws).hello()
            listed = conn.call("device.mine")["result"]
            assert listed[0]["online"] is True and listed[0]["model"] == "Fake Pixel"
            got = dcall(conn, op="compass.read", args={"seconds": 1})
            assert got["result"] == {"azimuth": [212.0]}
            refused = dcall(conn, op="apps.install")
            assert refused["error"]["code"] == "needs_newer_app"
        t.join(5)


def test_removing_a_device_closes_its_socket(web):
    app, c = web
    phone = Phone()
    pair(c, phone)
    with c.websocket_connect("/device") as dev:
        dev.send_json(phone.hello(dev.receive_json()))
        dev.receive_json()
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            conn = Conn(ws).hello()
            assert conn.call("device.remove", id=phone.id).get("error") is None
        with pytest.raises(WebSocketDisconnect):
            dev.receive_json()
    assert app.devices.get(phone.id) is None


def test_no_device_and_an_unknown_name_say_so(web):
    _, c = web
    with c.websocket_connect("/ws", headers=ORIGIN) as ws:
        conn = Conn(ws).hello()
        assert dcall(conn, op="device.info")["error"]["code"] == "no_device"
        assert (
            dcall(conn, op="device.info", device="nope")["error"]["code"]
            == "unknown_device"
        )
