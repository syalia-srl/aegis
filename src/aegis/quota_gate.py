"""Whether a token may start a harness now, from its policy and the pool gauges.

A policy maps a pool (a quota provider's name: ``claude``, ``opencode-go``, or
``codex``, which has no gauge) to a mode. ``closed`` starts nothing there;
``open`` starts anything; ``lines`` compares each listed window of the pool's
reading with a launch line and a stop line. Below every launch line the token
is open. At or above any stop line it is stopped: aegis interrupts its turns
(app.py). In between it is closed: running work goes on, nothing new starts. A
launch line of ``"pace"`` means the window's projected spend at reset is at
most 100%.

A line has three forms, shared by schedules, agents and queues (#1067): a
percent, the launch line on the percent used; ``"pace"``, which closes only
when the projection is above 100%, as it always has; or a map ``{used?,
projected?, stop?}`` naming at least one of used and projected, which closes
when used or projected reaches its line. ``stop`` is on the percent used:
real spend, not an estimate. ``lines_gate`` reads a ``quota:`` map of pools to
windows to lines. A closed gate names, in ``reopens``, every window that
holds it and the part of its line that tripped, and ``resets_at`` is the
latest of their resets: the time it opens at the latest.

A pool the policy does not name is closed, and so is a session that draws on
an account aegis reads no gauge for (OpenCode on a provider other than Go).
Codex has no gauge, so its policy is only ever closed or open. No policy at
all, the admin's case, is open. A reading older than STALE_S closes the gate
and never stops anything: a failed poll must not interrupt work. Pure: the
caller passes the snapshot ``Quota.snapshot()`` returns and the time.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TypeGuard, cast

from .quota import PROVIDERS

STALE_S = 900.0
# Pool name -> the harness that draws on it, and the windows a line may name.
POOLS = {p.name: p.harness for p in PROVIDERS} | {"codex": "codex"}
KINDS = {p.name: tuple(k for k, _ in p.bar_windows) for p in PROVIDERS}
MODES = ("closed", "open", "lines")


def pools_wire() -> list[dict]:
    """The pools a policy names and their windows, for the policy editor. A
    window with a known span can take a pace line."""
    return [
        {
            "name": p.name,
            "label": p.label,
            "windows": [
                {"kind": k, "label": label, "pace": k in p.window_spans}
                for k, label in p.bar_windows
            ],
        }
        for p in PROVIDERS
    ] + [{"name": "codex", "label": "Codex", "windows": []}]


@dataclass(frozen=True)
class Gate:
    state: str  # "open" | "closed" | "stopped"
    why: str = ""
    resets_at: float | None = None
    # When it opens again, for a gate a window's line closed: "drops below
    # 50%" or "is back on pace".
    # What must happen for it to open, one phrase per window that holds it:
    # "Claude 5 hours drops below 50%", "Claude weekly is back on pace".
    reopens: tuple[str, ...] = ()
    # The guard that closed it, "agent fair" or "queue fair-work" (guards.py).
    by: str = ""
    # False for an open gate whose lines have no reading to measure them
    # (lines_gate): open, but nothing says the window has room.
    measured: bool = True

    @property
    def may_start(self) -> bool:
        return self.state == "open"


def pool_for(harness: str, model: str | None) -> str | None:
    """The pool a session draws on; the client's providerFor (gauges.js)."""
    for name, h in POOLS.items():
        if h != harness:
            continue
        if harness == "opencode" and not (model or "").startswith(f"{name}/"):
            continue
        return name
    return None


def _reading(snapshot: dict, pool: str) -> dict | None:
    return next(
        (p for p in snapshot.get("providers", []) if p.get("name") == pool), None
    )


def line_of(v: object) -> dict | None:
    """A line in its map form, or None when ``v`` is not a line: a percent
    is ``{used: n}``, ``"pace"`` is ``{projected: 100}`` (which ``decide``
    reads strictly, see ``_pace``), and a map keeps its ``used``,
    ``projected`` and ``stop``."""
    if v == "pace":
        return {"projected": 100}
    if _percent(v):
        return {"used": v}
    if isinstance(v, dict):
        d = cast(dict, v)
        return {k: d[k] for k in ("used", "projected", "stop") if k in d}
    return None


def normal(line: object) -> dict:
    """A policy's line, either form, as ``{used?, projected?, stop?}``: the
    old ``{launch, stop}`` of a token policy, or a line ``line_of`` reads."""
    if isinstance(line, dict) and "launch" in line:
        d = cast(dict, line)
        out = line_of(d["launch"]) or {}
        return out | ({"stop": d["stop"]} if "stop" in d else {})
    return line_of(line) or {}


def _pace(line: object) -> bool:
    """Whether ``line`` is a ``"pace"`` line, in either policy form. Pace
    closes only above 100% projected, as before #1067; a map's ``projected``
    closes at its line."""
    if isinstance(line, dict) and "launch" in line:
        return cast(dict, line)["launch"] == "pace"
    return line == "pace"


def _reopens(n: dict, used_hit: bool, ahead_hit: bool, pace: bool) -> str:
    """What opens a window again: the part of its line that tripped."""
    used, ahead = n.get("used"), n.get("projected")
    parts = []
    if used_hit:
        parts.append(
            f"drops below {used}% used" if ahead is not None else f"drops below {used}%"
        )
    if ahead_hit:
        parts.append("is back on pace" if pace else f"is projected below {ahead}%")
    return " and ".join(parts) or f"drops below {n.get('stop')}%"


def line_errors(where: str, v: object) -> list[str]:
    """What is wrong with one line of a guard or a schedule."""
    n = line_of(v)
    if n is None:
        return [f"{where}: a line is a percent, pace or a map of used, projected, stop"]
    if isinstance(v, dict) and set(v) - {"used", "projected", "stop"}:
        extra = ", ".join(sorted(map(str, set(v) - {"used", "projected", "stop"})))
        return [f"{where}: a line takes used, projected and stop, not {extra}"]
    errs = []
    used, ahead, stop = n.get("used"), n.get("projected"), n.get("stop")
    if used is None and ahead is None:
        errs.append(f"{where}: a line names used, projected or both")
    if used is not None and (not _percent(used) or not 0 <= used <= 100):
        errs.append(f"{where}: used is 0 to 100")
    if ahead is not None and (not _percent(ahead) or not 1 <= ahead <= 1000):
        errs.append(f"{where}: projected is a whole percent from 1")
    if stop is not None:
        if not _percent(stop) or not 1 <= stop <= 100:
            errs.append(f"{where}: stop is 1 to 100")
        elif _percent(used) and used >= stop:
            errs.append(f"{where}: used is below stop")
    return errs


def quota_errors(quota: object) -> list[str]:
    """What is wrong with a ``quota:`` map of pools to windows to lines, the
    form schedules, agents and queues share."""
    if not isinstance(quota, dict):
        return ["quota is a map of pools to windows"]
    errs: list[str] = []
    for pool, ws in cast(dict, quota).items():
        pool = str(pool)
        if pool not in KINDS:
            errs.append(f"aegis reads no quota for {pool!r}")
            continue
        if not isinstance(ws, dict):
            errs.append(f"{pool}: a map of windows to lines")
            continue
        for kind, v in ws.items():
            if kind not in KINDS[pool]:
                have = ", ".join(KINDS[pool])
                errs.append(f"{pool} has no window {kind!r}; it has {have}")
                continue
            errs += line_errors(f"{pool} {kind}", v)
    return errs


def lines_gate(quota: dict, pool: str | None, snapshot: dict, now: float) -> Gate:
    """The gate of a ``quota:`` map for a start on ``pool``. A pool the map
    does not name, or that aegis has never read, has no line: there is
    nothing to measure, and holding would stop the work for good. A stale
    reading closes and never stops (decide)."""
    lines = quota.get(pool or "") if pool else None
    read = any(
        p.get("name") == pool and p.get("read_at") is not None
        for p in snapshot.get("providers", [])
    )
    if not pool or not lines:
        return Gate("open")
    if not read:
        return Gate("open", measured=False)
    return decide({pool: {"mode": "lines", "lines": lines}}, pool, snapshot, now)


def decide(policy: dict | None, pool: str | None, snapshot: dict, now: float) -> Gate:
    if policy is None:
        return Gate("open")
    if pool is None:
        return Gate("closed", "aegis reads no quota for this model's account")
    pp = policy.get(pool)
    if not isinstance(pp, dict) or pp.get("mode") == "closed":
        return Gate("closed", f"the policy does not open {pool}")
    if errs := validate({pool: pp}):
        return Gate("closed", f"the policy for {pool} is invalid: {'; '.join(errs)}")
    if pp.get("mode") == "open":
        return Gate("open")
    reading = _reading(snapshot, pool)
    if reading is None or reading.get("read_at") is None:
        return Gate("closed", f"there is no reading of {pool} yet")
    name = reading.get("label") or pool
    stale = now - float(reading["read_at"]) > STALE_S
    windows = {x.get("kind"): x for x in reading.get("windows", [])}
    stopped: str | None = None
    closed: str | None = None
    reopens: list[str] = []
    resets: list[float] = []
    for kind, line in (pp.get("lines") or {}).items():
        x = windows.get(kind)
        if x is None:
            continue
        label = x.get("label") or kind
        pct = float(x.get("percent") or 0)
        n = normal(line)
        used, ahead, stop = n.get("used"), n.get("projected"), n.get("stop")
        pace = _pace(line)
        proj = x.get("projected")
        used_hit = used is not None and pct >= float(used)
        ahead_hit = (
            ahead is not None
            and proj is not None
            and (proj > 100 if pace else proj >= ahead)
        )
        stop_hit = not stale and stop is not None and pct >= float(stop)
        if not (used_hit or ahead_hit or stop_hit):
            continue
        if stop_hit and stopped is None:
            stopped = f"{name} {label} is at {pct:.0f}%, stop line {stop}%"
        if closed is None and used_hit:
            closed = f"{name} {label} is at {pct:.0f}%, launch line {used}%"
        elif closed is None and ahead_hit:
            closed = f"{name} {label} is on pace for {proj}% at reset"
            if not pace:
                closed += f", launch line {ahead}%"
        reopens.append(f"{name} {label} {_reopens(n, used_hit, ahead_hit, pace)}")
        if x.get("resets_at") is not None:
            resets.append(float(x["resets_at"]))
    latest = max(resets) if resets else None
    if stopped is not None:
        return Gate("stopped", stopped, latest, tuple(reopens))
    if stale:
        return Gate(
            "closed",
            f"the last reading of {name} is older than {int(STALE_S // 60)} minutes",
        )
    if closed is not None:
        return Gate("closed", closed, latest, tuple(reopens))
    return Gate("open")


def _clock(ts: float) -> str:
    """A reset time: the hour within a day, else the day and the hour."""
    fmt = "%H:%M" if ts - time.time() < 86400 else "%a %H:%M"
    return time.strftime(fmt, time.localtime(ts))


def opens_again(g: Gate) -> str:
    """When ``g`` opens again, as a sentence that starts with a space: every
    window that holds it, and the latest of their resets."""
    if not g.reopens:
        return ""
    when = g.reopens[0] if len(g.reopens) == 1 else "all of: " + "; ".join(g.reopens)
    text = f" It opens again when {when}"
    if g.resets_at is not None:
        text += f", at the latest at {_clock(g.resets_at)}"
    return text + "."


def waits(g: Gate) -> str:
    """Why ``g`` is closed and when it opens again, in one sentence or two."""
    return f"{g.why}.{opens_again(g)}"


def refusal(owner: str, g: Gate) -> str:
    """Why ``owner`` may start nothing now, and when that changes."""
    return f"{owner} cannot start a session now: {g.why}.{opens_again(g)}"


def continued(owner: str, why: str) -> str:
    """The message a paused session gets when its gate opens again."""
    return (
        f"aegis paused your turn because {owner}'s quota line was reached ({why}).\n"
        "The pool has room again. Continue where you left off."
    )


def _percent(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def validate(policy: object) -> list[str]:
    """What is wrong with ``policy``; empty when it can be stored."""
    if not isinstance(policy, dict):
        return ["a policy is an object of pools"]
    errs: list[str] = []
    for pool, pp in policy.items():
        if not isinstance(pool, str) or pool not in POOLS:
            errs.append(f"unknown pool {pool!r}")
            continue
        modes = MODES if pool in KINDS else ("closed", "open")
        if not isinstance(pp, dict):
            errs.append(f"{pool}: mode is one of {', '.join(modes)}")
            continue
        pp = cast(dict, pp)
        if pp.get("mode") not in modes:
            errs.append(f"{pool}: mode is one of {', '.join(modes)}")
            continue
        lines = pp.get("lines") if pp["mode"] == "lines" else pp.get("lines") or {}
        if not isinstance(lines, dict):
            errs.append(f"{pool}: lines is an object of windows")
            continue
        for kind, line in lines.items():
            if not isinstance(kind, str) or kind not in KINDS[pool]:
                errs.append(f"{pool}: unknown window {kind!r}")
                continue
            if not (isinstance(line, dict) and "launch" in line):
                errs += line_errors(f"{pool} {kind}", line)
                continue
            launch, stop = line.get("launch"), line.get("stop")
            if not _percent(stop) or not 1 <= stop <= 100:
                errs.append(f"{pool} {kind}: stop is 1 to 100")
            elif launch != "pace" and (not _percent(launch) or not 0 <= launch < stop):
                errs.append(f"{pool} {kind}: launch is below stop, or pace")
    return errs


def describe(policy: dict | None) -> str:
    """A policy in one line, for ``aegis token list``."""
    if policy is None:
        return "never gated"
    parts = []
    for pool, pp in policy.items():
        pp = pp if isinstance(pp, dict) else {}
        if pp.get("mode") != "lines":
            parts.append(f"{pool}: {pp.get('mode', '?')}")
            continue
        lines = pp.get("lines") or {}
        shown = ", ".join(
            f"{k} {v.get('launch')}/{v.get('stop')}"
            for k, v in lines.items()
            if isinstance(v, dict)
        )
        parts.append(f"{pool}: {shown or 'open'}")
    return "; ".join(parts) or "every pool closed"
