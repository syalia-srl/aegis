"""Quota guards on agents and queues (#1067): their config, how they combine
up the chain of starters, what a queue and an agent do when one closes."""

import asyncio
import json

import pytest

from aegis.agents import agents_from
from aegis.guards import Guard
from aegis.queues import queues_from

BASE = {"harness": "claude-code", "model": "sonnet", "effort": "low"}
LINE = {"used": 80, "projected": 90, "stop": 97}


def agent(**extra):
    (a,) = agents_from({"agents": {"fair": BASE | {"permission": "full"} | extra}})
    return a


# -- config ------------------------------------------------------------------------
def test_an_agent_without_quota_is_unguarded():
    assert agent().guard is None and agent().error is None


def test_an_agent_guard_reads_its_lines_on_closed_and_advice():
    a = agent(quota={"claude": {"weekly_all": LINE}, "advice": "File it."})
    assert a.error is None
    assert a.guard == Guard({"claude": {"weekly_all": LINE}}, "warn", "File it.")
    assert agent(quota={"claude": {"session": 50}, "on_closed": "refuse"}).guard == (
        Guard({"claude": {"session": 50}}, "refuse")
    )


@pytest.mark.parametrize(
    "quota,says",
    [
        ({"claude": {"weekly_all": 80}, "on_closed": "reject"}, "refuse, warn"),
        ({"claude": {"weekly_all": {"stop": 90}}}, "used, projected or both"),
        ({"claude": {"weekly_all": 80}, "advice": 3}, "advice is a line"),
        ({"claude": {"rolling": 80}}, "has no window"),
        ("tight", "quota must be a map"),
    ],
)
def test_a_wrong_agent_guard_stops_the_agent(quota, says):
    a = agent(quota=quota)
    assert not a.enabled and says in (a.error or "")


def test_a_queue_guard_defaults_to_hold_and_takes_max_pending():
    qs = queues_from(
        {
            "fair-work": {
                "agent": "deepseek",
                "max_pending": 50,
                "quota": {"opencode-go": {"weekly": {"projected": 95, "stop": 99}}},
            },
            "plain": {"agent": "deepseek"},
        }
    )
    q = qs["fair-work"]
    assert q["guard"].on_closed == "hold" and q["max_pending"] == 50
    assert qs["plain"]["guard"] is None and qs["plain"]["max_pending"] is None


@pytest.mark.parametrize(
    "extra,says",
    [
        ({"max_pending": 0}, "max_pending 0 is not a whole number from 1 up"),
        ({"max_pending": True}, "max_pending True"),
        ({"quota": {"claude": {"session": 50}, "on_closed": "refuse"}}, "reject"),
        (
            {"quota": {"claude": {"session": 50}, "advice": "x"}},
            "a queue takes none",
        ),
    ],
)
def test_a_wrong_queue_guard_stops_the_queue(extra, says):
    q = queues_from({"q": {"agent": "a"} | extra})["q"]
    assert says in q.get("error", "")


# -- the combined gate -------------------------------------------------------------
from aegis.guards import levels, worst  # noqa: E402

NOW = 10_000.0


def snap(percent, projected=None, pool="claude", kind="weekly_all", read_at=NOW - 30):
    return {
        "providers": [
            {
                "name": pool,
                "label": pool.capitalize(),
                "read_at": read_at,
                "windows": [
                    {
                        "kind": kind,
                        "label": "weekly",
                        "percent": percent,
                        "projected": projected,
                        "resets_at": NOW + 3600,
                    }
                ],
            }
        ]
    }


TIGHT = Guard({"claude": {"weekly_all": 50}})
LOOSE = Guard({"claude": {"weekly_all": 90}})
STOP = Guard({"claude": {"weekly_all": {"used": 60, "stop": 70}}})


CC = {"harness": "claude-code", "model": "sonnet"}
GO = {"harness": "opencode", "model": "opencode-go/deepseek"}


def test_the_worst_of_agent_queue_and_starters_wins_and_names_its_level():
    recs = {
        "s-fair": {"agent": "fair", **CC},
        "s-boss": {"agent": "boss", "spawned_by": "s-fair", **CC},
    }
    worker = {"agent": "deepseek", "started_by": "s-boss", **CC}
    agents = {"fair": TIGHT, "boss": None, "deepseek": LOOSE}
    over = levels(worker, "fair-work", agents, {"fair-work": None}, recs.get, "claude")
    assert [label for label, _, _ in over] == ["agent deepseek", "agent fair"]
    g = worst(over, snap(55), NOW)
    assert g.state == "closed" and g.why.startswith("agent fair: ")
    assert g.by == "agent fair"
    assert worst(over, snap(40), NOW).state == "open"
    over = levels(worker, "fair-work", agents, {"fair-work": STOP}, recs.get, "claude")
    g = worst(over, snap(75), NOW)
    assert g.state == "stopped" and g.why.startswith("queue fair-work: ")


def test_no_guard_anywhere_is_open_and_a_loop_in_the_chain_ends():
    recs = {
        "a": {"agent": "x", "continues": "b"},
        "b": {"agent": "x", "spawned_by": "a"},
    }
    rec = {"agent": "x", "spawned_by": "a"}
    assert levels(rec, None, {}, {}, recs.get, "claude") == []
    assert worst([], snap(99), NOW).state == "open"


def test_a_guard_on_another_pool_does_not_hold():
    over = [("agent fair", TIGHT, "opencode-go")]
    assert worst(over, snap(99), NOW).state == "open"


def test_a_closed_starter_closes_a_worker_on_another_pool():
    """Spec section 07: fair's Claude line is closed, so a worker it enqueued
    on an OpenCode queue is closed too, read on fair's own pool."""
    recs = {"s-fair": {"agent": "fair", **CC}}
    worker = {"agent": "deepseek", "started_by": "s-fair", **GO}
    over = levels(worker, "fair-work", {"fair": TIGHT}, {}, recs.get, "opencode-go")
    assert over == [("agent fair", TIGHT, "claude")]
    g = worst(over, snap(86), NOW)
    assert g.state == "closed" and g.why.startswith("agent fair: Claude weekly")


def test_a_handoff_carries_guards_only_onto_the_same_agent():
    recs = {"old": {"agent": "fair", **CC}}
    same = {"agent": "fair", "continues": "old", **CC}
    other = {"agent": "opus", "continues": "old", **CC}
    agents = {"fair": TIGHT}
    assert [x[0] for x in levels(same, None, agents, {}, recs.get, "claude")] == [
        "agent fair"
    ]
    assert levels(other, None, agents, {}, recs.get, "claude") == []


def test_the_reopen_sentence_names_every_level_that_holds_and_the_latest_reset():
    from aegis.quota_gate import opens_again

    two = snap(86, 120)
    two["providers"][0]["windows"].append(
        {"kind": "session", "label": "5 hours", "percent": 70, "projected": None,
         "resets_at": NOW + 7200}
    )  # fmt: skip
    over = [
        ("agent fair", Guard({"claude": {"weekly_all": {"used": 80, "projected": 200}}}), "claude"),
        ("queue q", Guard({"claude": {"session": 60, "weekly_all": "pace"}}), "claude"),
    ]  # fmt: skip
    g = worst(over, two, NOW)
    assert g.reopens == (
        "Claude weekly drops below 80% used (agent fair)",
        "Claude 5 hours drops below 60% (queue q)",
        "Claude weekly is back on pace (queue q)",
    )
    assert g.resets_at == NOW + 7200
    assert opens_again(g).startswith(" It opens again when all of: Claude weekly")
    assert "at the latest at" in opens_again(g)


# -- the real app ------------------------------------------------------------------
import time  # noqa: E402

from .conftest import until  # noqa: E402
from .test_agents import World, turn  # noqa: E402

GUARDED = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  fair:
    harness: claude-code
    model: sonnet
    effort: low
    permission: full
    quota:
      claude: {weekly_all: {used: 80, projected: 90, stop: 97}}
      on_closed: %s
queues:
  general: {agent: opus}
  fair-work:
    agent: opus
    max_pending: 2
    quota:
      claude: {weekly_all: {used: 80}}
      on_closed: %s
"""


def gauge(world, percent: float, projected: float | None = None) -> None:
    """Every reading says Claude's week is at ``percent``% now."""
    now = time.time()
    world.app.quota.snapshot = lambda: {
        "providers": [
            {
                "name": "claude",
                "label": "Claude",
                "harness": "claude-code",
                "state": "ok",
                "read_at": now - 30,
                "windows": [
                    {
                        "kind": "weekly_all",
                        "label": "weekly",
                        "percent": percent,
                        "projected": projected,
                        "resets_at": now + 86400,
                    }
                ],
            }
        ]
    }


@pytest.fixture
def config():
    return ("warn", "hold")


@pytest.fixture
async def world(tmp_path, fake_claude, config):
    (tmp_path / ".aegis.yaml").write_text(GUARDED % config)
    w = await World(tmp_path, fake_claude).start()
    gauge(w, 10)
    yield w
    await w.stop()


async def spawn(world, agent: str = "fair"):
    r = await world.app.registry.call("session.spawn", {"agent": agent})
    return world.app.sessions.sessions[r["log_id"]]


async def test_a_stop_line_pauses_a_turn_and_its_reopening_continues_it(world):
    s = await spawn(world)
    await turn(s, "hello")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 98)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: s.quota_paused and s.status != "working", 8, "the stop")
    assert "agent fair: Claude weekly is at 98%, stop line 97%" in s.quota_paused
    world.app.on_quota(world.app.quota.snapshot())
    assert s.quota_paused  # still over the stop line: nothing continues
    gauge(world, 50)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: not s.quota_paused and s.status == "idle", 8, "the continue")
    sent = [e for e in s.entries() if e["kind"] == "inbox"]
    assert any("reached its stop line" in e["md"] for e in sent)


async def test_a_stale_reading_keeps_a_stopped_turn_stopped(world):
    """A reading 20 minutes old says nothing was spent less: the stop holds
    until a fresh one opens the guard (review of #1071)."""
    s = await spawn(world)
    await turn(s, "hello")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 98)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: s.quota_paused and s.status != "working", 8, "the stop")
    snap = world.app.quota.snapshot()
    snap["providers"][0]["read_at"] = time.time() - 1200
    world.app.quota.snapshot = lambda: snap
    world.app.on_quota(snap)
    await asyncio.sleep(0.5)
    assert s.quota_paused and s.status != "working"
    world.app.quota.snapshot = lambda: {"providers": []}  # never read
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.5)
    assert s.quota_paused and s.status != "working"
    assert await s.deliver("> from feed:tickets · 1 new", "ticket 7") == "held"
    gauge(world, 50)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: not s.quota_paused and not s.held, 8, "the continue")


async def test_a_persons_turn_is_never_cut_by_the_stop_line(world):
    s = await spawn(world)
    await turn(s, "hello")
    gauge(world, 98)
    await s.send("/busy 30", by_person=True)  # a person's message runs
    await until(lambda: s.status == "working", 5, "the person's turn")
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.5)
    assert s.status == "working" and not s.quota_paused
    await s.interrupt()


async def test_a_person_may_prompt_a_session_a_stop_line_cut(world):
    s = await spawn(world)
    await turn(s, "hello")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 98)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: s.quota_paused and s.status != "working", 8, "the stop")
    await world.app.registry.call("session.send", {"log_id": s.log_id, "text": "go on"})
    await until(lambda: any("go on" in t for t in sent(s)), 5, "the person's prompt")
    assert "guard:closed · critical" in sent(s)[-1]


async def test_an_unguarded_agent_is_never_stopped(world):
    s = await spawn(world, "opus")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 99)
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.3)
    assert s.status == "working" and not s.quota_paused
    await s.interrupt()


# -- queues ------------------------------------------------------------------------
from aegis.ops import OpError  # noqa: E402


async def enqueue(world, queue: str = "fair-work", payload: str = "/busy 20"):
    return await world.app.registry.call(
        "queue.enqueue", {"queue": queue, "payload": payload}
    )


@pytest.mark.parametrize("config", [("warn", "reject")])
async def test_a_rejecting_queue_refuses_with_why_and_when(world):
    gauge(world, 85)
    with pytest.raises(OpError) as e:
        await enqueue(world)
    assert e.value.code == "queue_closed"
    assert "queue fair-work: Claude weekly is at 85%, launch line 80%" in str(e.value)
    assert "opens again when Claude weekly drops below 80% (queue fair-work)" in str(
        e.value
    )
    assert not world.app.queues.tasks
    gauge(world, 10)
    r = await enqueue(world, payload="hi")
    assert "warning" not in r


@pytest.mark.parametrize("config", [("warn", "warn")])
async def test_a_warning_queue_takes_the_task_and_says_it_waits(world):
    gauge(world, 85)
    r = await enqueue(world, payload="hi")
    assert r["status"] == "pending" and "waits until the gate opens" in r["warning"]
    t = world.app.queues.tasks[r["task_id"]]
    gauge(world, 10)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: t.status == "completed", 12, "the task after the gate opened")


async def test_a_holding_queue_waits_silently_and_max_pending_bounds_it(world):
    gauge(world, 85)
    a, b = await enqueue(world), await enqueue(world)
    assert "warning" not in a and a["status"] == b["status"] == "pending"
    with pytest.raises(OpError) as e:
        await enqueue(world)
    assert e.value.code == "queue_full" and "2 pending" in str(e.value)


@pytest.mark.parametrize("config", [("warn", "hold")])
async def test_a_held_queue_leaves_every_slot_to_the_open_one(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        "max_workers: 2\n" + GUARDED % ("warn", "hold")
    )
    await until(lambda: world.app.config.current().max_workers == 2, 5, "the config")
    gauge(world, 85)
    await enqueue(world)
    await enqueue(world, "general")
    await enqueue(world, "general")
    q = world.app.queues
    await until(
        lambda: sum(t.status == "running" for t in q.tasks.values()) == 2, 8, "slots"
    )
    running = {t.queue for t in q.tasks.values() if t.status == "running"}
    assert running == {"general"}
    for t in list(q.tasks.values()):
        await q.cancel(t)


# -- agents ------------------------------------------------------------------------
from .test_agents import mcp  # noqa: E402


def sent(s) -> list[str]:
    """Every text the session sent its harness, in order."""
    recs = [json.loads(x) for x in s.store.path.read_text().splitlines() if x]
    return [r["text"] for r in recs if r.get("kind") == "send"]


async def test_a_warned_turn_carries_the_notice_once_then_the_open_notice(world):
    s = await spawn(world)
    gauge(world, 86, 104)
    await turn(s, "a ticket came in")
    first = sent(s)[-1]
    assert "> from aegis:notice · guard:closed · warn · " in first
    assert (
        "Your agent's quota guard is closed: agent fair: Claude weekly is at 86%"
        in first
    )
    assert "queue fair-work holds new tasks until it opens" in first
    assert "File what is asked as an issue" in first
    assert (
        "opens again when Claude weekly drops below 80% used and is projected"
        " below 90% (agent fair), at the latest at" in first
    )
    assert "aegis:notice · quota:" not in first  # the host's quota advice waits
    await turn(s, "another ticket")
    assert "guard:closed" not in sent(s)[-1]
    gauge(world, 10)
    await turn(s, "a third")
    assert "guard:closed · clear" in sent(s)[-1]
    assert "quota guard is open again" in sent(s)[-1]
    await turn(s, "a fourth")
    assert "guard:closed" not in sent(s)[-1]


async def test_the_notice_folds_as_its_own_row_and_reaches_a_turn_mid_way(world):
    s = await spawn(world)
    await turn(s, "hello")
    gauge(world, 86)
    await turn(s, "a ticket came in")
    rows = [e for e in s.entries() if ":notice:" in e["id"]]
    assert [e["title"].split(" · ")[1] for e in rows] == ["guard:closed"]
    said = [e for e in s.entries() if e["kind"] == "user"]
    assert said[-1]["md"] == "a ticket came in"  # not inside the prompt's row
    gauge(world, 10)
    world.app.on_quota(world.app.quota.snapshot())
    assert "guard:closed" in world.app.notices.pending(s, "tool")


async def test_guard_notices_off_by_source(world, tmp_path):
    (tmp_path / ".aegis.yaml").write_text(
        GUARDED % ("warn", "hold") + "notices: {guard: false}\n"
    )
    await until(
        lambda: world.app.config.current().notices.get("guard") is False, 5, "config"
    )
    s = await spawn(world)
    gauge(world, 86)
    await turn(s, "hello")
    assert "guard:closed" not in sent(s)[-1]


@pytest.mark.parametrize("config", [("refuse", "reject")])
async def test_a_refusing_agent_holds_a_wake_until_the_guard_opens(world):
    s = await spawn(world)
    await turn(s, "hello")
    gauge(world, 86)
    assert await s.deliver("> from feed:tickets · 1 new", "ticket 7") == "held"
    assert s.held and s.held[0]["held_for"] == "quota" and s.status == "idle"
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.3)
    assert s.held  # still closed
    gauge(world, 10)
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: not s.held and s.status == "idle", 8, "the held wake")
    assert any("ticket 7" in t for t in sent(s))


@pytest.mark.parametrize("config", [("refuse", "reject")])
async def test_a_refusing_agent_refuses_a_handoff_and_runs_a_persons_message(world):
    s = await spawn(world)
    await turn(s, "hello")
    boss = await spawn(world, "opus")
    gauge(world, 86)
    said = await turn(boss, mcp("peer_handoff", target=s.handle, context="do it"))
    assert said.startswith("mcp error: quota_closed")
    assert f"{s.handle} takes no handoff now: agent fair: Claude weekly" in said
    await turn(s, "a person asks", by_person=True)
    last = sent(s)[-1]
    assert "guard:closed" in last and "queue fair-work rejects new tasks" in last


async def test_an_agent_with_notices_off_gets_no_notice(world, tmp_path):
    text = (GUARDED % ("warn", "hold")).replace(
        "    permission: full\n    quota:",
        "    permission: full\n    notices: false\n    quota:",
    )
    (tmp_path / ".aegis.yaml").write_text(text)
    await until(
        lambda: (
            not next(
                a for a in world.app.config.current().agents if a.name == "fair"
            ).notices
        ),
        5,
        "the config",
    )
    s = await spawn(world)
    gauge(world, 86)
    await turn(s, "hello")
    assert "aegis:notice" not in sent(s)[-1]


@pytest.mark.parametrize("config", [("refuse", "reject")])
async def test_a_schedule_run_of_a_refusing_agent_is_skipped_with_why(world):
    from .test_schedules_e2e import create, frozen, runs, tick

    t = frozen(world)
    gauge(world, 86)
    create(world, "fair-sweep", "sweep", fire={"agent": "fair"}, quota={})
    await tick(world, t)
    (r,) = runs(world, "fair-sweep", "tick")
    assert r["out"] == "skipped" and r["line"].startswith("agent fair: Claude weekly")
    gauge(world, 10)
    await tick(world, t)
    assert len(runs(world, "fair-sweep", "fired")) == 1


# -- the worked example (spec section 07) ------------------------------------------
EXAMPLE = GUARDED.replace(
    "queues:\n",
    "  go:\n    harness: opencode\n    model: opencode-go/deepseek\n"
    "    effort: low\n    permission: full\n"
    "queues:\n"
    "  go-work:\n    agent: go\n    quota:\n"
    "      opencode-go: {monthly: {projected: 95, stop: 99}}\n"
    "      on_closed: reject\n",
)


async def test_a_closed_agent_cannot_spend_through_a_queue_on_another_pool(
    world, tmp_path
):
    (tmp_path / ".aegis.yaml").write_text(EXAMPLE % ("warn", "hold"))
    await until(lambda: "go-work" in world.app.queues.queues, 5, "the config")
    s = await spawn(world)
    await turn(s, "hello")
    gauge(world, 86)  # Claude only: OpenCode Go has no reading at all
    said = await turn(s, mcp("queue_enqueue", queue="go-work", payload="x"))
    assert said.startswith("mcp error: queue_closed")
    assert "agent fair: Claude weekly is at 86%" in said
    assert not world.app.queues.tasks
    await turn(s, "a ticket")
    assert "queue go-work rejects new tasks" in sent(s)[-1]
    gauge(world, 10)
    said = await turn(s, mcp("queue_enqueue", queue="go-work", payload="x"))
    assert "queue_closed" not in said


@pytest.mark.parametrize("config", [("refuse", "reject")])
async def test_a_persons_held_message_goes_alone_and_the_wakes_wait_in_order(world):
    s = await spawn(world)
    await turn(s, "hello")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 86)
    await s.deliver("> from feed:tickets · batch#1", "ticket 7")
    await s.deliver("> from agent:boss · handoff", "do it")
    await s.deliver("> from person", "a person asks", by_person=True)
    await s.interrupt()
    await until(lambda: any("a person asks" in t for t in sent(s)), 8, "the person's")
    assert not any("ticket 7" in t for t in sent(s))
    assert [m["body"] for m in s.held] == ["ticket 7", "do it"]
    assert all(m["held_for"] == "quota" for m in s.held)


def test_a_guard_with_no_pool_and_a_queue_advice_are_errors():
    from aegis.guards import AGENT_ACTIONS, QUEUE_ACTIONS, parse

    assert "names no pool" in (parse({"on_closed": "warn"}, AGENT_ACTIONS)[1] or "")
    assert "a queue takes none" in (
        parse({"claude": {"session": 50}, "advice": "x"}, QUEUE_ACTIONS)[1] or ""
    )


def test_a_schedule_line_takes_no_stop():
    from pydantic import ValidationError

    from aegis.schedule_log import LineMap

    with pytest.raises(ValidationError, match="takes no stop"):
        LineMap.model_validate({"used": 80, "stop": 97})
