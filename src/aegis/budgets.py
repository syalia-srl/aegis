"""Per-agent budgets (#1068).

An agent preset in ``.aegis.yaml`` may carry ``budget:``, a list of caps, each
``usd`` (the turn's cost as the harness reports it, the API-equivalent cost on
a subscription) or ``tokens`` (input plus output), ``per: session`` or per a
rolling window written as a duration (``24h``, ``7d``, ``30d``). A wrong cap
stops the agent with the error, as any other config error does.

Spend is charged by lineage: a session's turns count for its own agent and
for every agent up its chain of starters (guards.starters), so a capped agent
cannot spend without limit through a queue or a child. A session cap sums the
session and the sessions whose chain holds it; a window cap sums, over the
window, every session of the agent and every session whose chain holds one.
The turns are the usage rows (usage/tables.py), so spend is what was spent,
recorded at each turn's end: there is no estimate before a turn.

Past a cap the session is told once (source ``budget``) to finish and end its
turn, and its guard counts as closed (App.guard): from then on it starts no
turn a person did not start, whatever its quota ``on_closed`` says, and the
queues meet its enqueues with their own ``on_closed``. A window has room
again when enough of its oldest spend ages out (``frees_at``); a session cap
never frees.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import guards, notice_sources
from .journal import db
from .quota_gate import Gate
from .quota_gate import _clock as clock

# Its own notice row, off with notices: {budget: false}; its text is built
# from facts and keeps its end.
SOURCE = "budget"
notice_sources.declare(SOURCE, {}, cut=False)
MEASURES = ("usd", "tokens")
SPAN = re.compile(r"^([1-9][0-9]*)([hd])$")
# What marks a gate a budget closed (Gate.by), apart from a quota guard's.
BY = "budget "
FINISH = "Finish what you are doing as soon as possible and end your turn."


@dataclass(frozen=True)
class Cap:
    measure: str  # "usd" | "tokens"
    limit: float
    per: str  # "session" or a duration such as "7d"

    @property
    def span_s(self) -> float | None:
        """The window's length in seconds; None for a session cap."""
        m = SPAN.match(self.per)
        if m is None:
            return None
        return int(m[1]) * (3600 if m[2] == "h" else 86400)

    def describe(self) -> str:
        return f"{amount(self.measure, self.limit)} per {self.per}"


def amount(measure: str, value: float) -> str:
    if measure == "tokens":
        return f"{int(value):,} tokens"
    text = f"{value:,.2f}"
    return "$" + (text[:-3] if text.endswith(".00") else text)


def parse(raw: Any) -> tuple[tuple[Cap, ...], str | None]:
    """A ``budget:`` list as caps, and the error that stops its agent."""
    if raw is None or raw == []:
        return (), None
    if not isinstance(raw, list):
        return (), "budget must be a list of caps such as {usd: 2, per: session}"
    caps = []
    for c in raw:
        if not isinstance(c, dict):
            return (), f"budget cap {c!r} is not a map such as {{usd: 2, per: 7d}}"
        named = [m for m in MEASURES if m in c]
        if len(named) != 1:
            return (), "a budget cap names one of usd or tokens"
        if extra := sorted(str(k) for k in c if k not in (*MEASURES, "per")):
            return (
                (),
                f"a budget cap takes usd or tokens and per, not {', '.join(extra)}",
            )
        m = named[0]
        v = c[m]
        whole = m == "tokens"
        if (
            isinstance(v, bool)
            or not isinstance(v, int if whole else (int, float))
            or v <= 0
        ):
            kind = "whole number" if whole else "number"
            return (), f"budget {m} {v!r} is not a {kind} above 0"
        per = c.get("per")
        if per != "session" and not (isinstance(per, str) and SPAN.match(per)):
            return (), f"budget per {per!r} is session or a duration such as 24h or 7d"
        caps.append(Cap(m, float(v), str(per)))
    return tuple(caps), None


@dataclass(frozen=True)
class Row:
    """One turn's spend: a usage_turns row."""

    log_id: str
    ts: float
    usd: float
    tokens: int
    agent: str | None = None

    def value(self, measure: str) -> float:
        return self.usd if measure == "usd" else float(self.tokens)


def read(path: Path, since: float) -> list[Row]:
    """The turns in journal.db's usage rows after ``since``; none while the
    file or its table is not there yet, as before a backfill."""
    try:
        con = db.open_read(path)
        try:
            return [
                Row(*r)
                for r in con.execute(
                    "SELECT log_id, ts, cost, tok_in + tok_out, agent"
                    " FROM usage_turns WHERE ts > ?",
                    (since,),
                )
            ]
        finally:
            con.close()
    except sqlite3.Error:
        return []


Line = list[tuple[str | None, str | None]]  # (log_id, agent), the session first


def line(rec: dict, log_id: str | None, lookup: Callable[[str], dict | None]) -> Line:
    """The session and each one up its chain of starters (guards.starters),
    as (log_id, agent). ``lookup`` gives a record with its ``log_id``."""
    return [(log_id, rec.get("agent"))] + [
        (r.get("log_id"), r.get("agent")) for r in guards.starters(rec, lookup)
    ]


@dataclass(frozen=True)
class Over:
    agent: str
    cap: Cap
    spent: float
    # The session whose cap it is, for a session cap.
    session: str | None
    # When a window has room again; None for a session cap.
    frees_at: float | None


def since(
    own: Line,
    caps: dict[str, tuple[Cap, ...]],
    created: Callable[[str], float],
    now: float,
) -> float:
    """The oldest turn any cap over ``own`` reads."""
    out = now
    for lid, agent in own:
        for cap in caps.get(str(agent), ()):
            span = cap.span_s
            if span is not None:
                out = min(out, now - span)
            elif lid is not None:
                out = min(out, created(lid))
    return out


def over(
    own: Line,
    caps: dict[str, tuple[Cap, ...]],
    rows: list[Row],
    line_of: Callable[[Row], Line],
    now: float,
) -> Over | None:
    """The cap over ``own`` that is spent, or None when every one has room:
    a session cap before a window, since it never frees, else the window
    that frees last."""
    found: list[Over] = []
    seen: set[tuple[str, Cap]] = set()
    for lid, agent in own:
        for cap in caps.get(str(agent), ()):
            span = cap.span_s
            if span is None:
                if lid is None:
                    continue  # a session not started has spent nothing
                mine = [r for r in rows if lid in {x for x, _ in line_of(r)}]
            else:
                if (str(agent), cap) in seen:
                    continue
                seen.add((str(agent), cap))
                mine = [
                    r
                    for r in rows
                    if r.ts > now - span and agent in {a for _, a in line_of(r)}
                ]
            spent = sum(r.value(cap.measure) for r in mine)
            if spent < cap.limit:
                continue
            frees = None if span is None else frees_at(mine, cap, span)
            found.append(
                Over(str(agent), cap, spent, lid if span is None else None, frees)
            )
    if not found:
        return None
    return max(found, key=lambda o: float("inf") if o.frees_at is None else o.frees_at)


def frees_at(rows: list[Row], cap: Cap, span: float) -> float:
    """When enough of the window's oldest spend ages out that what is left
    is under the cap."""
    left = sum(r.value(cap.measure) for r in rows)
    for r in sorted(rows, key=lambda r: r.ts):
        left -= r.value(cap.measure)
        if left < cap.limit:
            return r.ts + span
    return max(r.ts for r in rows) + span  # not reached: the last row empties it


def why(o: Over, own: str | None, name: Callable[[str], str]) -> str:
    """ "agent fair has spent $25.40 of $25 in the last 7d"."""
    spent = amount(o.cap.measure, o.spent)
    if o.cap.measure == "tokens":
        spent = spent.removesuffix(" tokens")
    where = (
        f"in the last {o.cap.per}"
        if o.session is None
        else "in this session"
        if o.session == own
        else f"in session {name(o.session)}"
    )
    return f"agent {o.agent} has spent {spent} of {amount(o.cap.measure, o.cap.limit)} {where}"


def gate(o: Over, own: str | None, name: Callable[[str], str]) -> Gate:
    """The closed gate a spent cap sets: it reopens when the window frees,
    never for a session cap."""
    reopens = (
        ()
        if o.frees_at is None
        else (f"agent {o.agent}'s {o.cap.per} budget has room",)
    )
    return Gate(
        "closed",
        why(o, own, name),
        resets_at=o.frees_at,
        reopens=reopens,
        by=BY + o.agent,
    )


def is_budget(g: Gate) -> bool:
    return g.by.startswith(BY)


def notice(g: Gate) -> str:
    """What a session past a cap is told: the spend, to finish, and until
    when no turn a person did not start runs."""
    until = (
        f"until it has room again, at {clock(g.resets_at)}"
        if g.resets_at is not None
        else "again: a session cap never frees"
    )
    return (
        f"{g.why}. {FINISH} aegis starts no turn here that a person did not"
        f" start {until}."
    )


def again(g: Gate) -> str:
    """What it is told when the cap has room again."""
    return f"agent {g.by.removeprefix(BY)}'s budget has room again. Work as usual."
