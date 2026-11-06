"""The quota gate in the real app: the fake claude, a stub reading, a member."""

import asyncio
import json
import time

import pytest

from aegis.ops import Caller, Principal

from .conftest import until
from .test_agents import CONFIG, World

BEN = Principal("ben")
LINES = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50, "stop": 90}}}}


def seed(world, session=40.0, weekly=10.0, projected=None, read_at=None):
    """Make every reading the app takes say ``session``% of Claude's 5 hours."""
    now = time.time()

    def snapshot():
        return {
            "providers": [
                {
                    "name": "claude",
                    "label": "Claude",
                    "harness": "claude-code",
                    "state": "ok",
                    "read_at": now - 30 if read_at is None else read_at,
                    "windows": [
                        {
                            "kind": "session",
                            "label": "5 hours",
                            "percent": session,
                            "projected": projected,
                            "resets_at": now + 3600,
                        },
                        {
                            "kind": "weekly_all",
                            "label": "week",
                            "percent": weekly,
                            "projected": None,
                            "resets_at": now + 86400,
                        },
                    ],
                }
            ]
        }

    world.app.quota.snapshot = snapshot
    world.app.quota.check()


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    w.app.principals.add("ben", "member")
    await w.app.registry.call(
        "token.policy", {"name": "ben", "policy": LINES}, Caller("user")
    )
    yield w
    await w.stop()


def as_(principal):
    return Caller("user", principal=principal)


async def test_the_gate_follows_the_reading(world):
    app = world.app
    for pct, state in [(40, "open"), (63, "closed"), (91, "stopped"), (40, "open")]:
        seed(world, session=pct)
        assert app.gate("ben", "claude-code", "opus").state == state, pct
        assert app.gate(app.user, "claude-code", "opus").state == "open"


async def spawn_as(world, principal):
    seed(world, session=40)
    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus"}, as_(principal)
    )
    return world.session(r["log_id"])


async def test_a_closed_gate_keeps_a_members_tasks_pending_without_spinning(world):
    seed(world, session=63)
    q, cwd = world.app.queues, str(world.root)
    bens = [
        await asyncio.wait_for(
            q.enqueue("solo", "hello", False, None, cwd, owner="ben"), 3
        )
        for _ in range(2)
    ]
    admins = await asyncio.wait_for(q.enqueue("solo", "hello", False, None, cwd), 3)
    await until(lambda: admins.status == "completed", what="the admin's task")
    assert [t.status for t in bens] == ["pending", "pending"]
    assert "63%" in q.status(bens[0])["waiting_for_quota"]
    seed(world, session=40)
    await until(
        lambda: all(t.status == "completed" for t in bens),
        timeout=15,
        what="ben's tasks",
    )


def kinds(s, kind):
    return [e for e in s.entries() if e["kind"] == "system" and kind in e["summary"]]


def quota_inbox(s):
    return [
        e
        for e in s.entries()
        if e["kind"] == "inbox" and "from aegis:quota" in json.dumps(e)
    ]


async def working(s, text="/busy 30"):
    await s.send(text)
    await until(lambda: s.status == "working", what="the turn")


async def test_a_paused_worker_keeps_its_task_running_and_finishes_after(world):
    seed(world, session=40)
    q = world.app.queues
    t = await q.enqueue("solo", "/busy 30", False, None, str(world.root), owner="ben")
    await until(lambda: t.worker, what="the worker")
    w = world.session(t.worker)
    await until(lambda: w.status == "working", what="the worker's turn")
    seed(world, session=91)
    await until(lambda: w.status == "idle", what="the pause")
    await asyncio.sleep(0.3)
    assert t.status == "running"
    seed(world, session=40)
    await until(lambda: t.status == "completed", timeout=15, what="the task")


async def paused(world):
    ben = await spawn_as(world, BEN)
    await working(ben)
    seed(world, session=91)
    await until(lambda: ben.status == "idle" and ben.quota_paused, what="the pause")
    return ben


async def test_a_continue_that_cannot_start_stays_paused_and_tries_again(
    world, monkeypatch
):
    ben = await paused(world)
    await ben.stop()
    await ben.deliver("> from agent:ana · now", "keep this")
    real = type(ben).ensure_running
    fails = {"n": 1}

    async def flaky(self):
        if fails["n"]:
            fails["n"] -= 1
            raise FileNotFoundError("claude")
        await real(self)

    monkeypatch.setattr(type(ben), "ensure_running", flaky)
    seed(world, session=40)
    await until(lambda: fails["n"] == 0, what="the failed continue")
    await asyncio.sleep(0.2)
    assert ben.quota_paused and all(m.get("held_for") == "quota" for m in ben.held)
    assert "keep this" in json.dumps(ben.held)
    seed(world, session=40)
    await until(lambda: quota_inbox(ben), what="the second try")
    await until(lambda: ben.status == "idle" and not ben.held, what="its turn")
    assert "keep this" in json.dumps(ben.entries())


async def test_a_pause_or_continue_of_a_closed_session_does_nothing(world):
    ben = await paused(world)
    await world.app.sessions.close(ben.log_id)
    await world.app._continue(ben)
    assert not ben.running and ben.archived


async def test_the_restart_guard_holds_over_a_quota_continue(world):
    ben = await paused(world)
    world.app.sessions.declined.add(ben.log_id)
    seed(world, session=40)
    await asyncio.sleep(0.3)
    assert ben.quota_paused and not quota_inbox(ben)
