"""spawn, catalog and tags at every start (#959)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.policy import Catalog, Refused
from aegis.roots import make_roots
from aegis.session import SpawnSpec

from .conftest import until
from .policies import Named, Who

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  small: {harness: claude-code, model: sonnet, effort: high, permission: full}
queues:
  general: {agent: opus}
"""

ANA = Caller("user", principal=Principal("ana", Who("ana")))
BEN = Caller("user", principal=Principal("ben", Who("ben")))


@dataclass
class Gate(Named):
    refuse: str | None = None
    rewrite: dict = field(default_factory=dict)
    models: tuple[str, ...] | None = None
    seen: list = field(default_factory=list)

    async def spawn(self, who, req):
        self.seen.append((who, req))
        if self.refuse:
            raise Refused(self.refuse)
        return req.replace(**self.rewrite) if self.rewrite else req

    def catalog(self, who):
        return Catalog(models=self.models)


@pytest.fixture
async def world(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    gate = Gate()
    app = App(make_roots(tmp_path, None), claude_bin=fake_claude, policy=gate)
    yield SimpleNamespace(app=app, gate=gate, root=tmp_path)
    await app.shutdown()


async def spawn(app: App, caller: Caller = ANA, **params):
    r = await app.registry.call("session.spawn", {"agent": "opus", **params}, caller)
    return app.sessions.sessions[r["log_id"]]


async def test_spawn_sees_the_request_and_may_rewrite_it(world):
    world.gate.rewrite = {"model": "local-model", "tags": {"org": "north"}}
    s = await spawn(world.app)
    who, req = world.gate.seen[0]
    assert who == Who("ana") and req.agent == "opus" and req.harness == "claude-code"
    assert s.spec.model == "local-model" and s.spec.tags == {"org": "north"}
    assert s.spec.owner == "ana"
    assert world.app.view_of(s.log_id).tags == {"org": "north"}


async def test_a_request_env_reaches_the_process_and_not_the_meta(world):
    world.gate.rewrite = {"env": {"HOST_TOKEN": "s3cret"}}
    s = await spawn(world.app)
    assert s.host_env == {"HOST_TOKEN": "s3cret"}
    assert "s3cret" not in str(s.meta())


async def test_a_refusing_spawn_refuses_session_spawn(world):
    world.gate.refuse = "agents are off"
    with pytest.raises(Refused, match="agents are off"):
        await spawn(world.app)


async def test_a_refusing_spawn_refuses_a_handoff(world):
    s = await spawn(world.app)
    world.gate.refuse = "agents are off"
    with pytest.raises(OpError, match="agents are off"):
        await world.app.registry.call(
            "session.handoff", {"log_id": s.log_id, "msg": "go on"}, ANA
        )


async def test_a_refusing_spawn_fails_a_queue_task_with_the_hosts_message(world):
    world.gate.refuse = "agents are off"
    await world.app.registry.call(
        "queue.enqueue", {"queue": "general", "payload": "hi"}, ANA
    )
    await until(
        lambda: any(t.status == "failed" for t in world.app.queues.tasks.values()),
        timeout=10,
        what="the task to fail",
    )
    (t,) = world.app.queues.tasks.values()
    assert "agents are off" in str(t.error)


async def test_a_task_whose_owner_cannot_be_restored_fails_with_the_hosts_message(
    world,
):
    # Review focus: restart with an owner whose restore refuses.
    world.gate.gone.add("ana")
    world.app.whos.clear()
    await world.app.registry.call(
        "queue.enqueue", {"queue": "general", "payload": "hi"}, ANA
    )
    world.app.whos.clear()
    await until(
        lambda: any(t.status == "failed" for t in world.app.queues.tasks.values()),
        timeout=10,
        what="the task to fail",
    )
    (t,) = world.app.queues.tasks.values()
    assert "ana was deleted" in str(t.error)


async def test_a_resume_meets_a_refusing_spawn(world):
    s = await spawn(world.app)
    await s.send("/md hi")
    await until(lambda: s.resume_id is not None, timeout=10, what="a first turn")
    await world.app.registry.call("session.stop", {"log_id": s.log_id}, ANA)
    await until(lambda: not s.running, what="the stop")
    world.gate.refuse = "agents are off"
    with pytest.raises(OpError, match="agents are off"):
        await world.app.registry.call(
            "session.send", {"log_id": s.log_id, "text": "hi"}, ANA
        )
    assert not s.running


async def test_a_request_outside_the_catalog_is_refused_and_not_offered(world):
    world.gate.models = ("opus",)
    await spawn(world.app)
    with pytest.raises(Refused):
        await spawn(world.app, model="sonnet")
    listed = await world.app.registry.call("agents.list", {}, ANA)
    assert [a["name"] for a in listed["agents"]] == ["opus"]


async def test_a_spawned_session_inherits_its_parents_tags(world):
    world.gate.rewrite = {"tags": {"org": "north"}}
    parent = await spawn(world.app)
    world.gate.rewrite = {}
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus"}, Caller("agent", parent.log_id)
    )
    child = world.app.sessions.sessions[r["log_id"]]
    assert child.spec.tags == {"org": "north"} and child.spec.owner == "ana"
    assert world.gate.seen[-1][1].tags == {"org": "north"}


async def test_a_worker_of_a_hidden_persons_agent_keeps_owner_and_tags(world):
    # Review focus: inherited owner key and tags; hidden from the other caller.
    world.gate.rewrite = {"tags": {"org": "north"}}
    parent = await spawn(world.app)
    world.gate.rewrite = {}
    world.gate.hidden["ben"] = {"ana"}
    await world.app.registry.call(
        "queue.enqueue",
        {"queue": "general", "payload": "hi"},
        Caller("agent", parent.log_id),
    )
    await until(
        lambda: any(t.worker for t in world.app.queues.tasks.values()),
        timeout=10,
        what="the worker",
    )
    (t,) = world.app.queues.tasks.values()
    w = world.app.sessions.sessions[t.worker]
    assert w.spec.owner == "ana" and w.spec.tags == {"org": "north"}
    listed = await world.app.registry.call("session.list", {}, BEN)
    assert w.handle not in {r["handle"] for r in listed}


def test_tags_survive_the_spawn_record():
    s = SpawnSpec(
        agent="a",
        model="m",
        effort="high",
        permission="full",
        cwd=Path("/w"),
        tags={"org": "north"},
    )
    assert SpawnSpec.from_record(s.record(), Path("/x")).tags == {"org": "north"}


SWITCHES = {"harness_tools": False, "isolated": True, "primer": False, "notices": False}


async def test_spawn_may_set_the_four_product_switches(world):
    world.gate.rewrite = dict(SWITCHES)
    s = await spawn(world.app)
    _, req = world.gate.seen[0]
    assert (req.harness_tools, req.isolated, req.primer, req.notices) == (
        True,
        False,
        True,
        True,
    )
    assert {k: getattr(s.spec, k) for k in SWITCHES} == SWITCHES


def test_the_product_switches_survive_the_spawn_record():
    s = SpawnSpec(
        agent="a",
        model="m",
        effort="high",
        permission="full",
        cwd=Path("/w"),
        **SWITCHES,
    )
    back = SpawnSpec.from_record(s.record(), Path("/x"))
    assert {k: getattr(back, k) for k in SWITCHES} == SWITCHES
    plain = SpawnSpec(
        agent="a", model="m", effort="high", permission="full", cwd=Path("/w")
    )
    assert not set(SWITCHES) & set(plain.record())


async def test_a_harness_that_cannot_contain_refuses_the_start(world, monkeypatch):
    from aegis.claude.harness import ClaudeCode

    monkeypatch.setattr(ClaudeCode, "contains", frozenset({"isolated"}))
    world.gate.rewrite = dict(SWITCHES)
    with pytest.raises(OpError, match="cannot run with harness_tools yet"):
        await spawn(world.app)
