"""Every start picks its model: a cascade in the real app, with the fake
OpenCode and an agent of three models."""

import asyncio
import dataclasses
import time
from types import SimpleNamespace

import pytest

from aegis.cascade import Fall
from aegis.ops import OpError
from aegis.session import SpawnSpec

from .conftest import until
from .test_agents import World

CONFIG = """\
default_agent: free
agents:
  free:
    harness: opencode
    models: [opencode/a, opencode/b]
    effort: high
    permission: full
queues:
  general: {agent: free}
"""


def model_of(world, log_id: str) -> str:
    """A worker's model, read from its meta: a finished worker is archived at
    once, so it may be gone from the open sessions by the time a test looks."""
    metas, _ = world.app.sessions.metas.read_all()
    return next(m["model"] for m in metas if m.get("log_id") == log_id)


@pytest.fixture
async def world(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude, fake_opencode).start()
    yield w
    await w.stop()


async def spawn(world):
    r = await world.app.registry.call("session.spawn", {"agent": "free"})
    return world.session(r["log_id"])


async def test_spawn_starts_on_the_first_model_not_cooling(world):
    s = await spawn(world)
    assert s.spec.model == "opencode/a"
    assert s.spec.cascade == ("opencode/a", "opencode/b")
    world.app.cool("opencode", "opencode/a", Fall("model", None))
    s = await spawn(world)
    assert s.spec.model == "opencode/b"


async def test_spawn_is_refused_when_every_model_cools(world):
    for m in ("opencode/a", "opencode/b"):
        world.app.cool("opencode", m, Fall("model", None))
    with pytest.raises(OpError) as e:
        await spawn(world)
    assert e.value.code == "quota_closed"
    assert "every model in the cascade is cooling" in e.value.message


async def test_a_queue_task_waits_for_cooling_and_starts_when_it_ends(world):
    app = world.app
    for m in ("opencode/a", "opencode/b"):
        app.cool("opencode", m, Fall("model", 1.0))
    t = await asyncio.wait_for(
        app.queues.enqueue("general", "hello", False, None, str(world.root)), 3
    )
    assert t.status == "pending"
    assert app.cooling.earliest(time.time()) is not None
    await until(lambda: t.status != "pending", timeout=5, what="the cooling to end")
    assert model_of(world, t.worker) == "opencode/a"


async def test_a_handoff_keeps_a_model_chosen_outside_the_cascade(world):
    old = await spawn(world)
    await old.configure(model="opencode/z")
    for m in ("opencode/a", "opencode/b"):
        world.app.cool("opencode", m, Fall("model", None))
    r = await world.app.registry.call("session.handoff", {"log_id": old.log_id})
    assert world.session(r["log_id"]).spec.model == "opencode/z"


async def test_shutdown_cancels_the_cooling_timer(tmp_path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude, fake_opencode).start()
    app, released = w.app, []
    app._release_held = lambda: released.append(time.time())
    app.cool("opencode", "opencode/a", Fall("model", 0.5))
    await w.stop()
    await asyncio.sleep(0.8)
    assert released == []


def go_spec(world) -> SpawnSpec:
    """A claude-code cascade whose record names opencode-go: nothing sets a
    pool before the agent's connection does (B1), so the test sets it."""
    return SpawnSpec(
        agent="go-claude",
        model="glm-5",
        effort="high",
        permission="full",
        cwd=world.root,
        harness="claude-code",
        cascade=("glm-5", "kimi-k3"),
        pool="opencode-go",
    )


async def test_a_go_limit_on_claude_code_cools_go_and_not_claude(world):
    app, spec = world.app, go_spec(world)
    model, end = app.cascade_next(
        SimpleNamespace(spec=spec), "Claude AI usage limit reached|9999999999"
    )
    now = time.time()
    assert model is None and end == 9999999999.0
    assert app.cooling.until("claude-code", "kimi-k3", now, "opencode-go") == end
    assert app.cooling.until("claude-code", "opus", now, "claude") is None
    _, g = app.start_spec(spec, guarded=False)
    assert g.state == "closed" and "cooling" in g.why
    claude = dataclasses.replace(spec, pool=None, model="opus", cascade=("opus", "sonnet"))
    started, g = app.start_spec(claude, guarded=False)
    assert g.may_start and started.model == "opus"


async def test_a_model_fall_moves_a_pooled_cascade_to_its_next_model(world):
    app, spec = world.app, go_spec(world)
    model, _ = app.cascade_next(SimpleNamespace(spec=spec), "API Error: 529 Overloaded")
    assert model == "kimi-k3"
    started, g = app.start_spec(spec, guarded=False)
    assert g.may_start and started.model == "kimi-k3"
    assert app.cooling.until("claude-code", "glm-5", time.time()) is not None
