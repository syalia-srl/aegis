"""The host policy's public types, and the policy an App and a Server run (#959)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from aegis import Server
from aegis.app import App
from aegis.ops import OpError
from aegis.policy import Refused, SpawnRequest, StandalonePolicy
from aegis.roots import make_roots


def test_the_policy_names_are_public():
    import aegis

    for n in (
        "HostPolicy",
        "StandalonePolicy",
        "SpawnRequest",
        "SessionView",
        "Credential",
        "Catalog",
        "Event",
        "McpServer",
        "Refused",
    ):
        assert n in aegis.__all__ and getattr(aegis, n)


def test_refused_is_an_op_error_with_code_refused():
    e = Refused("agents are off for this account")
    assert isinstance(e, OpError) and e.code == "refused"
    assert e.message == "agents are off for this account"


def test_an_app_without_a_policy_runs_the_standalone_one(tmp_path):
    app = App(make_roots(tmp_path, None))
    assert type(app.policy) is StandalonePolicy
    assert app.policy.key(object()) == app.principals.owner


def test_a_spawn_request_replace_keeps_the_rest():
    r = SpawnRequest("a", "claude-code", "m", "high", "auto", Path("/w"), {}, None, {})
    assert r.replace(model="x") == dataclasses.replace(r, model="x")


def test_the_session_view_of_a_missing_session_is_none(tmp_path):
    app = App(make_roots(tmp_path, None))
    assert app.view_of("nope") is None


async def test_a_server_takes_a_policy(tmp_path, fake_claude):
    p = StandalonePolicy()
    async with Server(tmp_path, policy=p, claude=fake_claude) as srv:
        assert srv.app.policy is p
