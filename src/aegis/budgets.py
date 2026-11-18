"""Per-agent budgets (#1068).

An agent preset in ``.aegis.yaml`` may carry ``budget:``, a list of caps, each
``usd`` (the turn's cost as the harness reports it, the API-equivalent cost on
a subscription) or ``tokens`` (input plus output), ``per: session`` or per a
rolling window written as a duration (``24h``, ``7d``, ``30d``). A wrong cap
stops the agent with the error, as any other config error does.

Spend is charged by lineage: a session's turns count for its own agent and
for every agent up its chain of starters (guards.LINKS), so a capped agent
cannot spend without limit through a queue or a child. Unlike a quota guard's
chain (guards.starters), a budget's follows ``continues`` whatever the agent:
a handoff onto another agent stays on the old line, or a capped agent would
escape its cap through session_handoff(agent=...). A session cap sums the
session and the sessions whose chain holds it; a window cap sums, over the
window, every session of the agent and every session whose chain holds one.
The turns are the usage rows (usage/tables.py), so spend is what was spent,
recorded at each turn's end: there is no estimate before a turn.

The rows are read by lineage in SQL (``starter``, the nearest starter's log
id on each row), never the whole table, and each cap's result is kept
(Ledger): a session cap per (session, cap), a window per (agent, cap). The
server reads them again off the loop when a turn row is written; until then,
and while usage is rebuilt or backfilled, the kept result stands. A cap with
none kept stays closed while usage is rebuilt (REBUILDING).

Past a cap the session is told once (source ``budget``) to finish and end its
turn, and its guard counts as closed (App.guard): from then on it starts no
turn a person did not start, whatever its quota ``on_closed`` says, and the
queues meet its enqueues with their own ``on_closed``. A window has room
again when enough of its oldest spend ages out (``frees_at``); a session cap
never frees.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import notice_sources
from .guards import LINKS
from .journal import db
from .quota_gate import Gate
from .quota_gate import _clock as clock
from .usage import tables

# Its own notice row, off with notices: {budget: false}; its text is built
# from facts and keeps its end.
SOURCE = "budget"
notice_sources.declare(SOURCE, {}, cut=False)
MEASURES = ("usd", "tokens")
SPAN = re.compile(r"^([1-9][0-9]*)([hd])$")
# What marks a gate a budget closed (Gate.by), apart from a quota guard's.
BY = "budget "
FINISH = "Finish what you are doing as soon as possible and end your turn."
NEVER = "a session cap never frees"
# The why of a capped line while usage is rebuilt with no result kept.
REBUILDING = "usage is being rebuilt"
# The longest window a cap may name.
MAX_SPAN = 366 * 86400


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
    # Cents, or as many places as a small cap needs ($0.015), up to four.
    text = f"{value:,.4f}".rstrip("0")
    whole, cents = text.split(".")
    return "$" + (whole if not cents.strip("0") else f"{whole}.{cents:0<2}")


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
            or not math.isfinite(v)
            or v <= 0
        ):
            kind = "whole number" if whole else "number"
            return (), f"budget {m} {v!r} is not a {kind} above 0"
        per = c.get("per")
        if per != "session" and not (isinstance(per, str) and SPAN.match(per)):
            return (), f"budget per {per!r} is session or a duration such as 24h or 7d"
        cap = Cap(m, float(v), str(per))
        if (cap.span_s or 0) > MAX_SPAN:
            return (), f"budget per {per!r} is longer than 366d"
        caps.append(cap)
    return tuple(caps), None


@dataclass(frozen=True)
class Row:
    """One turn's spend: a usage_turns row."""

    log_id: str
    ts: float
    usd: float
    tokens: int

    def value(self, measure: str) -> float:
        return self.usd if measure == "usd" else float(self.tokens)


# The rows of a line: its seed sessions and every session whose nearest
# starter is in it, by the starter index; then their turns after ``ts``.
LINEAGE = """
WITH RECURSIVE line(id) AS ({seed}
  UNION SELECT u.log_id FROM usage_turns u JOIN line ON u.starter = line.id)
SELECT log_id, ts, cost, tok_in + tok_out FROM usage_turns
WHERE log_id IN line AND ts > ?"""
SEED = {
    "session": "SELECT ?",
    "agent": "SELECT DISTINCT log_id FROM usage_turns WHERE agent = ?",
}


def read(con: sqlite3.Connection, kind: str, seed: str, since: float) -> list[Row]:
    """The turns after ``since`` of session ``seed`` (kind ``session``) or
    of every session of agent ``seed`` (kind ``agent``), with every session
    down their chains."""
    sql = LINEAGE.format(seed=SEED[kind])
    return [Row(*r) for r in con.execute(sql, (seed, since))]


def row_record(con: sqlite3.Connection, log_id: str) -> dict | None:
    """A session aegis no longer knows, as its usage rows give it: its
    agent, nearest starter and first turn; None with no row."""
    agent, starter, first = con.execute(
        "SELECT max(agent), max(starter), min(ts) FROM usage_turns WHERE log_id = ?",
        (log_id,),
    ).fetchone()
    if first is None:
        return None
    return {
        "log_id": log_id,
        "agent": agent,
        "spawned_by": starter,
        "created_at": first,
    }


Line = list[tuple[str | None, str | None]]  # (log_id, agent), the session first


def line(rec: dict, log_id: str | None, lookup: Callable[[str], dict | None]) -> Line:
    """The session and each one up its chain of starters, nearest first, as
    (log_id, agent): every link of guards.LINKS, ``continues`` whatever the
    agent. A starter ``lookup`` does not know ends that branch."""
    out: Line = [(log_id, rec.get("agent"))]
    seen = {log_id}
    todo = [rec[k] for k in LINKS if rec.get(k)]
    while todo:
        lid = todo.pop(0)
        if lid in seen:
            continue
        seen.add(lid)
        if (r := lookup(lid)) is None:
            continue
        out.append((lid, r.get("agent")))
        todo += [r[k] for k in LINKS if r.get(k)]
    return out


@dataclass(frozen=True)
class Over:
    agent: str
    cap: Cap
    spent: float
    # The session whose cap it is, for a session cap.
    session: str | None
    # When a window has room again; None for a session cap.
    frees_at: float | None


# A cap's kept result: ("session", agent, log_id, cap) or ("agent", agent,
# None, cap), and what it reads from.
Key = tuple[str, str, str | None, Cap]


def keys(own: Line, caps: dict[str, tuple[Cap, ...]]) -> list[Key]:
    """The caps over ``own``: each session cap of a started session on it,
    and each window of an agent on it, once."""
    out: list[Key] = []
    for lid, agent in own:
        for cap in caps.get(str(agent), ()):
            if cap.span_s is None:
                if lid is not None:  # a session not started has spent nothing
                    out.append(("session", str(agent), lid, cap))
            elif (k := ("agent", str(agent), None, cap)) not in out:
                out.append(k)
    return out


def measure(
    con: sqlite3.Connection, key: Key, since: float | None, now: float
) -> Over | None:
    """Whether cap ``key`` is spent now, read by lineage. ``since``: when a
    session cap's session began (its created_at), None for its first row."""
    kind, agent, lid, cap = key
    span = cap.span_s
    if span is None:
        mine = read(con, "session", str(lid), (since or 0.0) - 1)
    else:
        mine = read(con, "agent", agent, now - span)
    spent = sum(r.value(cap.measure) for r in mine)
    if spent < cap.limit:
        return None
    frees = None if span is None else frees_at(mine, cap, span)
    return Over(agent, cap, spent, lid, frees)


def worst(found: list[Over]) -> Over | None:
    """The spent cap that names the gate: a session cap before a window,
    since it never frees, else the window that frees last."""
    if not found:
        return None
    return max(found, key=lambda o: float("inf") if o.frees_at is None else o.frees_at)


def _complete(con: sqlite3.Connection) -> bool:
    try:
        return tables.is_complete(con)
    except sqlite3.Error:
        return True  # no usage tables yet: nothing to rebuild


class Ledger:
    """Each cap's last result, read from journal.db's usage rows.

    ``over`` answers from what is kept and reads a cap with nothing kept, or
    a window past the time it frees, on the caller's thread: once per cap.
    ``refresh`` reads every kept cap again, on a thread of its own, when a
    turn row was written. ``busy`` is true while usage is rebuilt: what is
    kept stands, and a cap with nothing kept is REBUILDING."""

    def __init__(self, path: Path, busy: Callable[[], bool]) -> None:
        self.path = path
        self.busy = busy
        self.kept: dict[Key, Over | None] = {}
        self._rows: dict[str, dict | None] = {}
        self.reads = 0  # caps read, for the tests

    def _open(self) -> sqlite3.Connection | None:
        try:
            return db.open_read(self.path)
        except sqlite3.Error:
            return None

    def over(
        self, ks: list[Key], since: Callable[[Key], float | None], now: float
    ) -> Over | str | None:
        """The spent cap among ``ks``, None when each has room, or the agent
        of one with nothing kept while usage is rebuilt."""
        found = []
        stale = [
            k
            for k in ks
            if k not in self.kept
            or ((o := self.kept[k]) is not None and o.frees_at is not None
                and now >= o.frees_at)
        ]  # fmt: skip
        if stale and self.busy():
            if missing := [k for k in stale if k not in self.kept]:
                return missing[0][1]
            stale = []
        if stale:
            got = self.measure({k: since(k) for k in stale}, now)
            if got is None:  # rebuilt by another process: as busy
                if missing := [k for k in stale if k not in self.kept]:
                    return missing[0][1]
            else:
                self.kept.update(got)
        for k in ks:
            if (o := self.kept.get(k)) is not None:
                found.append(o)
        return worst(found)

    def measure(
        self, jobs: dict[Key, float | None], now: float
    ) -> dict[Key, Over | None] | None:
        """Read each cap of ``jobs`` (with its since) once; a cap not
        readable yet, as before the first backfill, has room. None while
        the file's backfill is not complete: a rebuild, perhaps by
        ``aegis journal rebuild`` in another process, has not put every row
        back. Thread-safe."""
        con = self._open()
        out: dict[Key, Over | None] = {}
        try:
            if con is not None and not _complete(con):
                return None
            for k, since in jobs.items():
                self.reads += 1
                try:
                    out[k] = None if con is None else measure(con, k, since, now)
                except sqlite3.Error:
                    out[k] = None
        finally:
            if con is not None:
                con.close()
        return out

    def record(self, log_id: str) -> dict | None:
        """A session aegis no longer knows, from its rows (row_record), once."""
        if log_id not in self._rows:
            con = self._open()
            try:
                self._rows[log_id] = None if con is None else row_record(con, log_id)
            except sqlite3.Error:
                return None
            finally:
                if con is not None:
                    con.close()
        return self._rows[log_id]


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
    if g.resets_at is None:
        after = f"aegis starts no more turns here that a person did not start: {NEVER}."
    else:
        after = (
            "aegis starts no turn here that a person did not start until it"
            f" has room again, at {clock(g.resets_at)}."
        )
    return f"{g.why}. {FINISH} {after}"


def again(o: Over) -> str:
    """What it is told when the cap has room again."""
    return f"agent {o.agent}'s {o.cap.per} budget has room again."


def never_frees(g: Gate) -> bool:
    """A gate a spent session cap closed: no time opens it."""
    return is_budget(g) and g.resets_at is None and g.why != REBUILDING
