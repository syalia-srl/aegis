"""Quota guards on agents and queues (#1067): their config, how they combine
up the chain of starters, what a queue and an agent do when one closes."""

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
