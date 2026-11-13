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
        d.on_reply(live, {"id": frame["id"], "error": {"code": "denied", "message": "no"}})

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
