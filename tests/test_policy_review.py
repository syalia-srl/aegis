"""Findings of #959's final review, each pinned by a test."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis import Server
from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.policy import Refused, StandalonePolicy
from aegis.policy_view import PolicyView
from aegis.roots import make_roots
from aegis.session import SpawnSpec

from .conftest import until
from .policies import Named, Who

ANA = Caller("user", principal=Principal("ana", Who("ana")))
BEN = Caller("user", principal=Principal("ben", Who("ben")))
CONFIG = (
    "default_agent: opus\nagents:\n"
    "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
)
NEW = {
    "name": "ana-triage",
    "clock": {"every": "5m"},
    "fire": {"agent": "opus"},
    "prompt": "triage",
}


def spec(cwd: Path, owner: str, **kw) -> SpawnSpec:
    return SpawnSpec(
        agent="opus",
        model="opus",
        effort="high",
        permission="full",
        cwd=cwd,
        owner=owner,
        **kw,
    )


@pytest.fixture
async def world(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    policy = Named(hidden={"ben": {"ana"}})
    app = App(make_roots(tmp_path, None), claude_bin=fake_claude, policy=policy)
    yield SimpleNamespace(app=app, policy=policy, root=tmp_path)
    await app.shutdown()


# -- C1 and I1: server-wide operations ----------------------------------------


@pytest.mark.parametrize(
    "op,params",
    [
        ("link.add", {"name": "far", "url": "ws://127.0.0.1:1/ws", "token": "t"}),
        ("config.write", {"text": "x"}),
        ("server.pause", {}),
        ("token.revoke", {}),
        ("term.spawn", {"profile": "shell"}),
        ("fs.list", {"path": "."}),
        ("usage.read", {}),
    ],
)
async def test_a_hosts_caller_cannot_run_a_server_wide_operation(world, op, params):
    with pytest.raises(OpError) as e:
        await world.app.registry.call(op, params, ANA)
    assert e.value.code in ("refused", "bad_params"), e.value
    if e.value.code == "bad_params":
        pytest.fail(f"{op}: fix the test's params: {e.value.message[:120]}")


def test_every_server_wide_op_is_flagged(world):
    flagged = {o.name for o in world.app.registry._ops.values() if o.server}
    for name in ("link.add", "config.write", "term.spawn", "fs.write"):
        assert name in flagged


async def test_the_standalone_callers_keep_server_wide_operations(tmp_path):
    app = App(make_roots(tmp_path, None))
    owner = await app.identify(
        __import__("aegis.policy", fromlist=["Credential"]).Credential(
            app.principals.admin_token(), "link", server="far"
        )
    )
    caller = Caller("user", link="far", principal=owner)
    assert await app.registry.call("link.list", {}, caller) is not None


# -- C2: schedules belong to their person -------------------------------------


async def test_a_person_cannot_see_or_change_another_persons_schedule(world):
    app = world.app
    await app.registry.call("schedule.create", NEW, ANA)
    assert [s["name"] for s in await app.registry.call("schedule.list", {}, ANA)] == [
        "ana-triage"
    ]
    assert await app.registry.call("schedule.list", {}, BEN) == []
    for op in ("schedule.fire", "schedule.pause", "schedule.delete"):
        with pytest.raises(OpError) as e:
            await app.registry.call(op, {"name": "ana-triage"}, BEN)
        assert e.value.code == "no_schedule", op
    got = []
    app.channels.subscribe("schedules", got.append, view=PolicyView(app, app.who(BEN)))
    assert got[0]["data"] == []


# -- I2 and I3: ids, toasts, monitors -----------------------------------------


async def test_browser_ops_by_id_declare_the_browser(world):
    ops = world.app.registry._ops
    for name in ("browser.input", "browser.nav", "browser.take", "browser.reopen"):
        assert ("id", "browser") in ops[name].names, name
    assert ("monitor_id", "monitor") in ops["monitor.cancel"].names


async def test_a_hidden_sessions_toast_does_not_reach_another_person(world):
    app = world.app
    s = await app.sessions.spawn(spec(world.root, "ana"))
    got = []
    app.channels.subscribe("toasts", got.append, view=PolicyView(app, app.who(BEN)))
    app.toast("secret plan", source=s)
    assert all("secret plan" not in str(m) for m in got)


async def test_monitor_list_shows_a_person_only_what_they_see(world):
    app = world.app
    s = await app.sessions.spawn(spec(world.root, "ana"))
    app.monitors.start(
        s.log_id, str(world.root), description="ana's wait", done="false"
    )
    listed = await app.registry.call("monitor.list", {}, BEN)
    assert all(m["description"] != "ana's wait" for m in listed)
    listed = await app.registry.call("monitor.list", {}, ANA)
    assert any(m["description"] == "ana's wait" for m in listed)


# -- I4: the library API --------------------------------------------------------


async def test_as_who_reads_and_lists_only_what_the_person_may(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(
        tmp_path, policy=Named(hidden={"ben": {"ana"}}), claude=fake_claude
    ) as srv:
        s = await srv.as_who(Who("ana")).sessions.spawn()
        ben = srv.as_who(Who("ben"))
        from aegis.api.sessions import Session, SessionNotFound

        with pytest.raises(SessionNotFound):
            await Session(ben, s.log_id).entries()
        assert await ben.workflows.list() == []
        assert await ben.schedules.list() == []


# -- I5: tags are shown to no one ----------------------------------------------


async def test_tags_never_reach_a_browser(world):
    app = world.app
    s = await app.sessions.spawn(spec(world.root, "ana", tags={"org": "north"}))
    assert "tags" not in s.wire()
    await app.sessions.close(s.log_id)
    page = await app.registry.call("archive.list", {}, ANA)
    assert all("tags" not in row for row in page["items"])


# -- I6 and I7: refused wakes ---------------------------------------------------


async def test_a_refused_wake_leaves_the_hosts_message_in_the_transcript(world):
    app = world.app
    s = await app.sessions.spawn(spec(world.root, "ana"))
    await s.send("/md hi")
    await until(lambda: s.resume_id is not None, timeout=10, what="a first turn")
    await s.stop()
    world.policy.gone.add("ana")
    app.whos.clear()
    with pytest.raises(Refused):
        await s.ensure_running()
    assert any("ana was deleted" in str(e) for e in s.entries()[-3:])


async def test_every_start_after_the_first_asks_spawn(world):
    app = world.app
    s = await app.sessions.spawn(spec(world.root, "ana"))
    await s.stop()
    assert s.resume_id is None  # no turn yet: no harness session id
    world.policy.gone.add("ana")
    app.whos.clear()
    with pytest.raises(Refused):
        await s.ensure_running()


# -- I8: the server's own key, and events ---------------------------------------


async def test_a_host_key_equal_to_the_servers_user_is_refused(world):
    class Clash(Named):
        def key(self, who):
            return world.app.user

    app = App(make_roots(world.root / "x", None), policy=Clash())
    with pytest.raises(Refused):
        await app.identify(
            __import__("aegis.policy", fromlist=["Credential"]).Credential(
                "tok-ana", "browser"
            )
        )
    with pytest.raises(Refused):
        app.token_for(Who("ana"))


async def test_an_event_names_its_sessions_owner(world):
    got = []

    class Audit(Named):
        async def on_event(self, who, event):
            got.append(event)

    app = App(make_roots(world.root / "y", None), policy=Audit())
    app.event("start", "nope", {})
    await until(lambda: got, what="the event")
    assert got[0].owner is None


# -- the standalone server pays nothing for the policy -------------------------


def test_the_standalone_policy_neither_narrows_nor_listens(tmp_path):
    app = App(make_roots(tmp_path, None), policy=StandalonePolicy())
    assert not app.narrows() and not app.listens()
