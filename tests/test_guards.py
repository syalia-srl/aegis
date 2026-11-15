"""Quota guards on agents and queues (#1067): their config, how they combine
up the chain of starters, what a queue and an agent do when one closes."""

import asyncio

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
            "no quota for 'advice'",
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


def test_the_worst_of_agent_queue_and_starters_wins_and_names_its_level():
    recs = {
        "s-fair": {"agent": "fair"},
        "s-boss": {"agent": "boss", "spawned_by": "s-fair"},
    }
    worker = {"agent": "deepseek", "started_by": "s-boss"}
    agents = {"fair": TIGHT, "boss": None, "deepseek": LOOSE}
    over = levels(worker, "fair-work", agents, {"fair-work": None}, recs.get)
    assert [label for label, _ in over] == ["agent deepseek", "agent fair"]
    g = worst(over, "claude", snap(55), NOW)
    assert g.state == "closed" and g.why.startswith("agent fair: ")
    assert g.by == "agent fair"
    assert worst(over, "claude", snap(40), NOW).state == "open"
    over = levels(worker, "fair-work", agents, {"fair-work": STOP}, recs.get)
    g = worst(over, "claude", snap(75), NOW)
    assert g.state == "stopped" and g.why.startswith("queue fair-work: ")


def test_no_guard_anywhere_is_open_and_a_loop_in_the_chain_ends():
    recs = {
        "a": {"agent": "x", "continues": "b"},
        "b": {"agent": "x", "spawned_by": "a"},
    }
    assert levels({"agent": "x", "spawned_by": "a"}, None, {}, {}, recs.get) == []
    assert worst([], "claude", snap(99), NOW).state == "open"


def test_a_guard_on_another_pool_does_not_hold():
    over = [("agent fair", TIGHT)]
    assert worst(over, "opencode-go", snap(99), NOW).state == "open"


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
    assert any("quota line was reached" in e["md"] for e in sent)


async def test_an_unguarded_agent_is_never_stopped(world):
    s = await spawn(world, "opus")
    await s.send("/busy 30")
    await until(lambda: s.status == "working", 5, "the long turn")
    gauge(world, 99)
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.3)
    assert s.status == "working" and not s.quota_paused
    await s.interrupt()
