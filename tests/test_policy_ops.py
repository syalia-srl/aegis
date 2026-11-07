"""guard and around in Registry.call (#959)."""

from __future__ import annotations

from pathlib import Path

import pytest

from aegis.app import App
from aegis.ops import USER, Caller, OpError
from aegis.policy import HostPolicy, Refused, StandalonePolicy
from aegis.roots import make_roots


def app_with(tmp_path: Path, policy) -> App:
    return App(make_roots(tmp_path, None), policy=policy)


async def test_around_sees_the_op_and_can_change_the_result(tmp_path):
    seen = []

    class P(StandalonePolicy):
        async def around(self, op, who, params, call):
            seen.append(op)
            out = await call(params)
            return {"wrapped": out} if op == "archive.list" else out

    app = app_with(tmp_path, P())
    assert "wrapped" in await app.registry.call("archive.list", {})
    assert seen == ["archive.list"]


async def test_around_gets_validated_params(tmp_path):
    got = []

    class P(StandalonePolicy):
        async def around(self, op, who, params, call):
            got.append(params)
            raise Refused("stop here")

    app = app_with(tmp_path, P())
    with pytest.raises(OpError) as e:
        await app.registry.call("term.spawn", {"bogus": 1})
    assert e.value.code == "bad_params" and got == []
    with pytest.raises(Refused):
        await app.registry.call("term.spawn", {"profile": "shell"})
    assert got[0].profile == "shell"


async def test_the_default_guard_keeps_todays_refusals(tmp_path, monkeypatch):
    app = app_with(tmp_path, StandalonePolicy())
    reg = app.registry
    monkeypatch.setattr(reg, "permission_of", lambda log_id: "read")
    with pytest.raises(OpError) as e:
        await reg.call("session.spawn", {}, Caller("agent", "s1"))
    assert e.value.code == "read_only" and "turn_end" in e.value.message
    monkeypatch.setattr(reg, "permission_of", lambda log_id: "full")
    monkeypatch.setattr(reg, "schedule_of", lambda log_id: {"run": 3, "name": "n"})
    with pytest.raises(OpError) as e:
        await reg.call("session.spawn", {}, Caller("agent", "s1"))
    assert e.value.code == "spawn_off"
    with pytest.raises(OpError) as e:
        await reg.call("server.version", {}, Caller("agent", "s1"))
    assert e.value.code == "not_for_agents"
    with pytest.raises(OpError) as e:
        await reg.call("fs.list", {"path": "."}, Caller("user", link="far"))
    assert e.value.code == "not_across_links"


async def test_a_guard_that_drops_super_drops_todays_checks(tmp_path, monkeypatch):
    class Open(HostPolicy):
        def guard(self, op, who, caller):
            return None

    app = app_with(tmp_path, Open())
    # archive.list is closed to agents only by the default guard.
    assert await app.registry.call("archive.list", {}, Caller("agent", "s1"))


async def test_around_refusing_term_spawn_refuses_it_from_the_client(tmp_path):
    # Review focus: a host refuses terminals through around. term.spawn is
    # closed to agents by the default guard, ahead of around.
    class NoShells(StandalonePolicy):
        async def around(self, op, who, params, call):
            if op.startswith("term."):
                raise Refused("no shells here")
            return await call(params)

    app = app_with(tmp_path, NoShells())
    with pytest.raises(Refused, match="no shells here"):
        await app.registry.call("term.spawn", {"profile": "shell"}, USER)
    with pytest.raises(OpError) as e:
        await app.registry.call(
            "term.spawn", {"profile": "shell"}, Caller("agent", "s1")
        )
    assert e.value.code == "not_for_agents"
