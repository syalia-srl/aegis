"""Callers under a host policy: identify, key and restore, minted tokens (#959)."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegis import host_tokens
from aegis.app import App
from aegis.ops import Caller
from aegis.policy import Credential, HostPolicy, Refused, StandalonePolicy
from aegis.roots import make_roots
from aegis.web import PROTO, build_web
from .policies import Named, RefuseAll, Who

TOKEN = "t0ken"
ORIGIN = {"origin": "http://testserver"}


def app_with(tmp_path: Path, policy) -> App:
    return App(make_roots(tmp_path, None), policy=policy)


def close_code(app: App, token: str) -> int | None:
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({"t": "hello", "token": token, "proto": PROTO})
            try:
                assert ws.receive_json()["t"] == "welcome"
            except WebSocketDisconnect as e:
                return e.code
    return None


def test_a_minted_token_round_trips_and_expires():
    s = b"k" * 32
    t = host_tokens.mint(s, "ana", ttl=60, now=1000)
    assert host_tokens.verify(s, t, now=1059) == "ana"
    assert host_tokens.verify(s, t, now=1061) is None
    assert host_tokens.verify(b"x" * 32, t, now=1000) is None
    assert host_tokens.verify(s, t[:-2] + "AA", now=1000) is None
    assert host_tokens.verify(s, "aeg1.garbage", now=1000) is None


def test_the_secret_is_kept_private_and_reused(tmp_path):
    a = host_tokens.load_or_create_secret(tmp_path)
    assert len(a) == 32 and host_tokens.load_or_create_secret(tmp_path) == a
    assert (tmp_path / "host-secret").stat().st_mode & 0o777 == 0o600


async def test_a_minted_token_survives_a_restart(tmp_path):
    app = app_with(tmp_path, Named())
    token = app.token_for(Who("ana"), ttl=600)
    again = app_with(tmp_path, Named())
    p = await again.identify(Credential(token, "browser"))
    assert p.name == "ana" and p.who == Who("ana")


async def test_a_raw_token_goes_to_the_hosts_identify(tmp_path):
    app = app_with(tmp_path, Named())
    p = await app.identify(Credential("tok-ben", "browser"))
    assert p.name == "ben" and p.who == Who("ben")


async def test_the_servers_own_token_is_its_own_caller_under_any_policy(tmp_path):
    app = app_with(tmp_path, Named())
    p = await app.identify(Credential(app.principals.admin_token(), "browser"))
    assert p.name == app.user and p.who is None


def test_a_refused_identify_closes_the_socket_4401(tmp_path):
    assert close_code(app_with(tmp_path, RefuseAll()), "anything") == 4401
    assert close_code(app_with(tmp_path, Named()), "tok-ana") is None


async def test_a_link_hello_is_refused_by_hostpolicy_and_taken_by_standalone(tmp_path):
    host = app_with(tmp_path / "h", HostPolicy())
    with pytest.raises(Refused):
        await host.identify(
            Credential(host.principals.admin_token(), "link", server="far")
        )
    app = app_with(tmp_path / "s", StandalonePolicy())
    p = await app.identify(
        Credential(app.principals.admin_token(), "link", server="far")
    )
    assert p.name == app.principals.owner


def test_an_expired_minted_token_closes_an_open_socket(tmp_path, monkeypatch):
    # Review focus: expiry while the tab is open.
    app = app_with(tmp_path, Named())
    token = app.token_for(Who("ana"), ttl=60)
    with TestClient(build_web(app, TOKEN, {"testserver"})) as c:
        with c.websocket_connect("/ws", headers=ORIGIN) as ws:
            ws.send_json({"t": "hello", "token": token, "proto": PROTO})
            assert ws.receive_json()["t"] == "welcome"
            real = host_tokens.time.time
            monkeypatch.setattr(host_tokens.time, "time", lambda: real() + 120)
            ws.send_json({"t": "call", "id": 1, "op": "server.version", "params": {}})
            # The next thing the socket does is close: no reply to the call.
            with pytest.raises(WebSocketDisconnect) as e:
                got = ws.receive_json()
                pytest.fail(f"the socket stayed open and sent {got}")
            assert e.value.code == 4401


async def test_an_agents_who_is_restored_from_its_sessions_owner(tmp_path):
    app = app_with(tmp_path, Named())
    assert await app.who_of_key("ana") == Who("ana")
    with pytest.raises(Refused, match="cara was deleted"):
        app.policy.gone.add("cara")
        await app.who_of_key("cara")


async def test_ensure_who_fills_the_cache_the_sync_who_reads(tmp_path, monkeypatch):
    app = app_with(tmp_path, Named())
    monkeypatch.setattr(app, "owner_of", lambda log_id: "ana")
    agent = Caller("agent", "s1")
    await app.ensure_who(agent)
    assert app.who(agent).name == "ana" and app.who(agent).who == Who("ana")
