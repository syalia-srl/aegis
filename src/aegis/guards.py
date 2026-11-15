"""Quota guards on agents and queues (#1067).

An agent preset or a queue in ``.aegis.yaml`` may carry ``quota:``, the form a
schedule's quota has: a map from a pool to its windows, each with a line
(quota_gate.line_of). Beside the pools sit ``on_closed`` and, for an agent,
``advice``. An agent or queue with no ``quota:`` has no guard and is never
held: the maintainer's own sessions must never feel the quota.

A wrong ``quota:`` stops the agent or the queue with the error, as any other
config error does, rather than running it unguarded.

The gate a start or a turn passes is the worst of the agent's guard, for a
queue worker its queue's, and the guards of the agents up its chain of
starters (``spawned_by``, ``started_by``, ``continues``): each is a ceiling
someone set, and none may lift another's, so a guarded agent cannot spend
through an open queue or a wider child. Each guard is read on the pool the
session draws on, and from ``.aegis.yaml`` as it is now: unlike a scope, a
guard tightened on disk applies to sessions already running.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .quota_gate import Gate, lines_gate, quota_errors

AGENT_ACTIONS = ("refuse", "warn")
QUEUE_ACTIONS = ("reject", "warn", "hold")


@dataclass(frozen=True)
class Guard:
    # Pools to windows to lines, as the file writes them.
    lines: dict = field(default_factory=dict)
    on_closed: str = "warn"
    advice: str | None = None


def parse(raw: Any, actions: tuple[str, ...]) -> tuple[Guard | None, str | None]:
    """A ``quota:`` block as a Guard, None when there is none, and the error
    that stops its agent or queue. ``actions``: what ``on_closed`` may say,
    the first being the default for a queue, the last for an agent."""
    if raw is None:
        return None, None
    if not isinstance(raw, dict):
        return None, "quota must be a map of pools, on_closed and advice"
    d = dict(raw)
    agent = actions == AGENT_ACTIONS
    default = "warn" if agent else "hold"
    on_closed = d.pop("on_closed", default)
    advice = d.pop("advice", None) if agent else None
    if on_closed not in actions:
        return None, f"quota on_closed is one of {', '.join(actions)}"
    if advice is not None and not (isinstance(advice, str) and advice.strip()):
        return None, "quota advice is a line of text"
    if errs := quota_errors(d):
        return None, f"quota: {'; '.join(errs)}"
    return Guard(lines=d, on_closed=str(on_closed), advice=advice), None


def max_pending(raw: Any) -> tuple[int | None, str | None]:
    """A queue's ``max_pending``, None when left out, and its error."""
    if raw is None:
        return None, None
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        return None, f"max_pending {raw!r} is not a whole number from 1 up"
    return raw, None


RANK = {"open": 0, "closed": 1, "stopped": 2}
LINKS = ("spawned_by", "started_by", "continues")


def starters(rec: dict, lookup: Callable[[str], dict | None]) -> list[dict]:
    """The records up ``rec``'s chain of starters, nearest first. A starter
    aegis no longer knows ends that branch."""
    seen: set[str] = set()
    out: list[dict] = []
    todo = [rec[k] for k in LINKS if rec.get(k)]
    while todo:
        log_id = todo.pop(0)
        if log_id in seen:
            continue
        seen.add(log_id)
        if (r := lookup(log_id)) is None:
            continue
        out.append(r)
        todo += [r[k] for k in LINKS if r.get(k)]
    return out


def levels(
    rec: dict,
    queue: str | None,
    agents: dict[str, Guard | None],
    queues: dict[str, Guard | None],
    lookup: Callable[[str], dict | None],
) -> list[tuple[str, Guard]]:
    """Every guard over a start of ``rec`` (a spec's record or a meta), by
    the level that set it: its agent, its queue, the agents of its starters."""
    named = [(f"agent {rec.get('agent')}", agents.get(str(rec.get("agent"))))]
    if queue:
        named.append((f"queue {queue}", queues.get(queue)))
    named += [
        (f"agent {r.get('agent')}", agents.get(str(r.get("agent"))))
        for r in starters(rec, lookup)
    ]
    out: dict[str, Guard] = {}
    for label, g in named:
        if g is not None and label not in out:
            out[label] = g
    return list(out.items())


def worst(
    guards: list[tuple[str, Guard]], pool: str | None, snapshot: dict, now: float
) -> Gate:
    """The most restrictive gate of ``guards`` on ``pool``, open < closed <
    stopped, its why prefixed with the level that set it."""
    out = Gate("open")
    for label, g in guards:
        x = lines_gate(g.lines, pool, snapshot, now)
        if RANK[x.state] > RANK[out.state]:
            out = dataclasses.replace(x, why=f"{label}: {x.why}", by=label)
    return out
