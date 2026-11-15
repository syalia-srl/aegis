"""Quota guards on agents and queues (#1067).

An agent preset or a queue in ``.aegis.yaml`` may carry ``quota:``, the form a
schedule's quota has: a map from a pool to its windows, each with a line
(quota_gate.line_of). Beside the pools sit ``on_closed`` and, for an agent,
``advice``. An agent or queue with no ``quota:`` has no guard and is never
held: the maintainer's own sessions must never feel the quota.

A wrong ``quota:`` stops the agent or the queue with the error, as any other
config error does, rather than running it unguarded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .quota_gate import quota_errors

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
