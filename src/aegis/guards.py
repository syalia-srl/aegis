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
through an open queue or a wider child. A handoff (``continues``) carries the
old session's guards only onto the same agent: a handoff onto another agent
starts a new line, and scopes already limit which agents an agent may reach.

Each level is read on the pool its own agent draws on: the agent's and the
queue's on the pool of the session being started, a starter's on the pool of
that starter's model. A closed starter therefore closes its descendants on any
pool: an agent whose Claude line is closed cannot spend through a queue whose
workers run on OpenCode. Guards are read from ``.aegis.yaml`` as it is now:
unlike a scope, a guard tightened on disk applies to sessions already running.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import notice_sources
from .quota_gate import Gate, lines_gate, pool_for, quota_errors

AGENT_ACTIONS = ("refuse", "warn")
# The notice's third line when the agent gives no advice.
ADVICE = "File what is asked as an issue or a task line and say it will be done later."
# The notice source that tells a session its guard is closed (App.guard_items):
# its own row, off with notices: {guard: false}. Its text is several lines
# built from facts, so the one-line cut does not apply.
SOURCE = "guard"
notice_sources.declare(SOURCE, {}, cut=False)
OPEN_AGAIN = "Your agent's quota guard is open again. Work as usual."
# What marks a turn a guard's stop line cut, in quota_paused, apart from the
# person's pause (pause.WHY): a person's message to it runs, a wake waits.
CUT = "a quota guard's stop line: "


def cut(why: str) -> str:
    return CUT + why


def is_cut(paused: str | None) -> bool:
    return bool(paused) and str(paused).startswith(CUT)


def uncut(paused: str | None) -> str:
    return str(paused or "").removeprefix(CUT)


def continued(why: str) -> str:
    """What a session a stop line cut gets when its guard is read open."""
    return (
        f"aegis paused your turn because your quota guard reached its stop line"
        f" ({why}).\nThe pool has room again. Continue where you left off."
    )


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
    if raw is None or raw == {}:
        return None, None
    if not isinstance(raw, dict):
        return None, "quota must be a map of pools, on_closed and advice"
    d = dict(raw)
    agent = actions == AGENT_ACTIONS
    default = "warn" if agent else "hold"
    on_closed = d.pop("on_closed", default)
    if not agent and "advice" in d:
        return None, "quota advice is for an agent's notice; a queue takes none"
    advice = d.pop("advice", None) if agent else None
    if not d:
        return None, "quota names no pool, so it guards nothing: add a pool's lines"
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
    aegis no longer knows ends that branch, and so does a handoff onto
    another agent: ``continues`` links two sessions of one agent only."""
    seen: set[str] = set()
    out: list[dict] = []
    todo = list(_links(rec))
    while todo:
        log_id, agent = todo.pop(0)
        if log_id in seen:
            continue
        seen.add(log_id)
        if (r := lookup(log_id)) is None:
            continue
        if agent is not None and r.get("agent") != agent:
            continue
        out.append(r)
        todo += _links(r)
    return out


def _links(rec: dict) -> list[tuple[str, str | None]]:
    """Each starter ``rec`` names, with the agent it must run to count: a
    handoff's old session counts only when it ran the same agent."""
    return [
        (rec[k], rec.get("agent") if k == "continues" else None)
        for k in LINKS
        if rec.get(k)
    ]


def levels(
    rec: dict,
    queue: str | None,
    agents: dict[str, Guard | None],
    queues: dict[str, Guard | None],
    lookup: Callable[[str], dict | None],
    pool: str | None,
) -> list[tuple[str, Guard, str | None]]:
    """Every guard over a start of ``rec`` (a spec's record or a meta) on
    ``pool``, by the level that set it, with the pool it is read on: its
    agent's and its queue's on ``pool``, each starter's agent's on the pool
    that starter draws on."""
    named = [(f"agent {rec.get('agent')}", agents.get(str(rec.get("agent"))), pool)]
    if queue:
        named.append((f"queue {queue}", queues.get(queue), pool))
    named += [
        (
            f"agent {r.get('agent')}",
            agents.get(str(r.get("agent"))),
            pool_for(str(r.get("harness") or ""), r.get("model")),
        )
        for r in starters(rec, lookup)
    ]
    out: dict[tuple[str, str | None], Guard] = {}
    for label, g, p in named:
        if g is not None and (label, p) not in out:
            out[(label, p)] = g
    return [(label, g, p) for (label, p), g in out.items()]


def worst(
    guards: list[tuple[str, Guard, str | None]], snapshot: dict, now: float
) -> Gate:
    """The most restrictive gate of ``guards``, each on its own pool, open <
    closed < stopped, its why prefixed with the level that set it. It opens
    again when every level that holds it does: ``reopens`` names each one's
    windows, and ``resets_at`` is the latest of their resets."""
    out = Gate("open")
    reopens: list[str] = []
    resets: list[float] = []
    measured = True
    for label, g, pool in guards:
        x = lines_gate(g.lines, pool, snapshot, now)
        measured = measured and x.measured
        if x.state == "open":
            continue
        reopens += [f"{r} ({label})" for r in x.reopens]
        if x.resets_at is not None:
            resets.append(x.resets_at)
        if RANK[x.state] > RANK[out.state]:
            out = Gate(x.state, f"{label}: {x.why}", by=label)
    if out.state == "open":
        return Gate("open", measured=measured)
    latest = max(resets) if resets else None
    return dataclasses.replace(out, reopens=tuple(reopens), resets_at=latest)
