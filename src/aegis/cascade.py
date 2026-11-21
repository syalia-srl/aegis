"""Cascades: which failures move a session to its next model, and which model.

Zen's free usage limit, measured 2026-10-18 and 2026-10-20 (the second run on
OpenCode v2.0.28): about 520 ``opencode run -m opencode/beam-free "say ok"``
calls over two runs (37 and 4 minutes) never got a 429 FreeUsageLimitError, so
its scope is not reproduced and stays ``model``. What came instead was an error
for the whole host: ``provider.auth`` (status 403, FreeTierError) "OpenCode's
free models aren't available from your current network because we've detected
abusive traffic from this IP address", on big-pickle, deepseek-v4.1-flash-free
and glm-5.3-flash-free, while beam-free still answered. It is an auth error, so
it stays: every other free model would fail the same way.

An agent's ``models:`` is a cascade (agents.py). This module is its pure part:
``classify`` reads a failed turn's error text and answers whether the session
falls (and how widely the failure reaches) or stays; ``Cooling`` remembers the
models that fell, so new spawns skip them; ``pick`` chooses the first model
after a position that is neither cooling nor closed by its quota gate.

Only a failure of the model or its provider falls: quota, rate limits,
overloads, 5xx, a model the provider no longer serves. An interrupt, a context
or auth error, and anything unrecognized stay, because the next model would
fail the same way or a real defect would hide behind a weaker model. The stay
patterns are checked first. Codex has no patterns yet, so nothing on it falls.

A ``pool`` failure is the account's, not the model's: Claude Code's usage
limit cools every model that draws on the session's pool, which its caller
names (quota_gate.pool_of), so a Go limit on a claude-code session cools Go
and not Opus. A pool fall is keyed by the pool alone: two clients of one
account share it. With no pool named, an endpoint's fall is its own (two
unmetered gateways never share one), and a harness derives the pool as
pool_for does, else keys the model's provider prefix under itself, so it
never cools another harness's pool. A harness's recorded ``pool: none`` has
no pool: its pool fall cools the model alone (_pool_key).

A model fall is keyed by its owner (``cooling_owner``): a connection's
endpoint, else the harness. ``cool`` writes and ``until`` and ``pick`` read
under that same owner, so the key a fall was written under is the key a pick
skips.

The cooling table lives in memory, per server. A restart clears it, which
costs at most one failed turn per model. A pick never wraps: falling only goes
down the list.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from .quota_gate import NONE, Gate, pool_for

# How long a model cools when its error names no retry-after.
DEFAULT_COOL_S = 600.0

Scope = Literal["model", "pool"]


@dataclass(frozen=True)
class Fall:
    scope: Scope
    retry_after_s: float | None
    # The epoch the error names as the reset (Claude Code's trailing
    # ``|<epoch>``); it outranks retry_after_s. Not part of equality: two
    # falls of one kind are the same answer whenever their limit resets.
    until: float | None = field(default=None, compare=False)


# Zen's free usage limit: see the findings at the top of this docstring.
ZEN_FREE_SCOPE: Scope = "model"

_STAY = [
    re.compile(p, re.I)
    for p in (
        # "aborted due to timeout" is a fetch timeout, which falls.
        r"\babort(?!ed due to timeout)",
        r"interrupt",
        # Not "context deadline exceeded" (a gateway timeout), nor the
        # "maximum tokens requested" of Anthropic's rate-limit message.
        r"context(?! deadline)|too long|maximum context",
        r"auth|api[ _-]?key|unauthori[sz]ed|\b40[13]\b",
        r"invalid request|\b400\b",
    )
]
_PROVIDER: list[tuple[re.Pattern[str], Scope]] = [
    (re.compile(p, re.I), "model")
    for p in (
        r"\b429\b|rate.?limit|too many requests",
        r"\b5\d\d\b|overloaded|unavailable|timed? ?out|deadline exceeded",
        # Only a not-found that names the model: a lost session stays.
        r"ModelNotFound|\bmodel\b.*\bnot.?found",
    )
]
_FALL: dict[str, list[tuple[re.Pattern[str], Scope]]] = {
    "opencode": [(re.compile(r"FreeUsageLimit", re.I), ZEN_FREE_SCOPE), *_PROVIDER],
    "claude-code": [
        (
            re.compile(r"usage limit|weekly limit|hit your limit|\d-hour limit", re.I),
            "pool",
        ),
        *_PROVIDER,
    ],
}
_RETRY = re.compile(r"retry(?:[ -]after)?:? (?:in )?(\d+(?:\.\d+)?) ?s", re.I)
_EPOCH = re.compile(r"\|(\d{9,})\s*$")


def classify(harness: str, text: str) -> Fall | None:
    """The fall a failed turn's error text asks for, or None to stay."""
    if any(p.search(text) for p in _STAY):
        return None
    for pattern, scope in _FALL.get(harness, ()):
        if pattern.search(text):
            retry = _RETRY.search(text)
            epoch = _EPOCH.search(text)
            return Fall(
                scope,
                float(retry.group(1)) if retry else None,
                float(epoch.group(1)) if epoch else None,
            )
    return None


def cooling_owner(harness: str, base_url: str | None) -> str:
    """The owner a model fall is keyed by: a connection's endpoint, else the
    harness, so two gateways never share one and an endpoint id never
    collides with a harness's."""
    return base_url or harness


def _is_endpoint(owner: str) -> bool:
    return "://" in owner


def _pool_key(owner: str, model: str, pool: str | None) -> tuple[str, str, str] | None:
    """The key a pool fall of ``model`` is written and read under. A named
    pool keys alone, so two clients of one account share it; an endpoint
    with none (``pool: none``) keys itself. A harness's ``none`` has no
    pool, so its pool fall cools the model alone (None). A harness that
    names nothing keys the pool pool_for derives, which is the pool pool_of
    would name, else the model's provider prefix under the harness, so a
    ``claude/x`` on opencode never cools Claude Code's ``claude``."""
    if pool and pool != NONE:
        return (pool, "pool", pool)
    if _is_endpoint(owner):
        return (owner, "pool", owner)
    if pool == NONE:
        return None
    if derived := pool_for(owner, model):
        return (derived, "pool", derived)
    return (owner, "pool", model.split("/", 1)[0])


class Cooling:
    """Models and pools that fell, each with the time it is free again."""

    def __init__(self) -> None:
        # (owner, "model", model) or (pool, "pool", pool) -> until
        self._until: dict[tuple[str, str, str], float] = {}

    def cool(
        self, owner: str, model: str, fall: Fall, now: float, pool: str | None = None
    ) -> float:
        if fall.until is not None and fall.until > now:
            end = fall.until
        elif fall.retry_after_s is not None:
            end = now + fall.retry_after_s
        else:
            end = now + DEFAULT_COOL_S
        key = (owner, "model", model)
        if fall.scope == "pool":
            key = _pool_key(owner, model, pool) or key
        self._until[key] = max(end, self._until.get(key, 0.0))
        return end

    def until(
        self, owner: str, model: str, now: float, pool: str | None = None
    ) -> float | None:
        ends = [self._until.get((owner, "model", model), 0.0)]
        if (key := _pool_key(owner, model, pool)) is not None:
            ends.append(self._until.get(key, 0.0))
        end = max(ends)
        return end if end > now else None

    def latest(
        self,
        harness: str,
        models: str | Iterable[str],
        now: float,
        users: Iterable[tuple[str, str | None]] = (),
    ) -> float | None:
        """When a catalogue row's model is free again: the latest end among
        the harness's own key and the keys of the agents that name it, each
        given as (cooling_owner, pool), under every spelling of the model (its
        id and the id it resolves to: a fall is written under the one the
        session ran). A row is a catalogue, not a session, so it has no
        endpoint of its own; its agents supply theirs."""
        names = [models] if isinstance(models, str) else list(dict.fromkeys(models))
        users = list(users)
        ends = []
        for model in names:
            ends.append(self.until(harness, model, now))
            ends += [self.until(owner, model, now, pool) for owner, pool in users]
        return max((e for e in ends if e is not None), default=None)

    def earliest(self, now: float) -> float | None:
        return min((u for u in self._until.values() if u > now), default=None)

    def wire(self, now: float) -> list[dict]:
        out = []
        for (owner, kind, name), u in sorted(self._until.items()):
            if u <= now:
                continue
            if kind == "pool":
                out.append({"pool": name, "until": u})
            else:
                by = "endpoint" if _is_endpoint(owner) else "harness"
                out.append({by: owner, "model": name, "until": u})
        return out


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def pick(
    models: Sequence[str],
    after: int,
    owner: str,
    gate: Callable[[str], Gate],
    cooling: Cooling,
    now: float,
    pool_of: Callable[[str], str | None] = lambda m: None,
) -> tuple[str | None, str]:
    """The first model below position ``after`` (-1 at spawn) that is neither
    cooling nor gated, or None and why, naming when the first is free again.
    ``owner`` is the one cool wrote the fall under (cooling_owner), and
    ``pool_of`` the pool of each candidate: a cascade with no connection can
    change pool from one model to the next."""
    frees: list[float] = []
    for model in models[after + 1 :]:
        if (end := cooling.until(owner, model, now, pool_of(model))) is not None:
            frees.append(end)
            continue
        g = gate(model)
        if g.may_start:
            return model, ""
        if g.resets_at:
            frees.append(g.resets_at)
    why = "every model in the cascade is cooling or gated"
    if frees:
        why += f"; the first is free again at {_clock(min(frees))}"
    return None, why
