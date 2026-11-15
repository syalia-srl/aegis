"""Quota: how much of each subscription window is left, for the gauges.

``Quota`` holds one ``QuotaService`` per provider and runs one loop. Every
``TICK_S`` it asks each service to refresh, and the service's own floor decides
whether that really fetches (Claude every 180 s, OpenCode Go every 60 s). So one
loop carries both the fetch cadence and the re-evaluation of pace, which moves
with the clock. It then builds the wire snapshot and publishes it on the
``quota`` channel if it differs from the last one published; ``projected`` and
the times are rounded so that happens at most once a tick.

Python decides severity and the projection; the browser draws the tick and the
countdown from ``starts_at`` and ``resets_at`` (DESIGN.md, "Python decides, the
browser draws"). Reading the snapshot never fetches, so ``quota.read`` costs an
agent nothing against the vendor.

The cache under ``cache_dir()`` is shared by every aegis process on the
machine, the legacy tree's included: quota is an account property, and the
Claude endpoint 429s when several pollers ask (#41).
"""

from __future__ import annotations

import hashlib

import asyncio
import logging
import os
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .claude import PROVIDER as CLAUDE, parse_stream
from .core import (
    PACE_USUAL_DEFAULT,
    FAILURE_TEXT,
    QuotaProvider,
    QuotaService,
    QuotaState,
    boot_clock,
    cancel_and_wait,
    QuotaWindow,
    severity_from,
    window_pace,
)
from .opencode import PROVIDER as OPENCODE_GO
from .projection import Projector, half_life

log = logging.getLogger("aegis.quota")

PROVIDERS: tuple[QuotaProvider, ...] = (CLAUDE, OPENCODE_GO)
TICK_S = 60.0
Publish = Callable[[str, list[dict]], None]


def cache_dir() -> Path:
    """Where every aegis process on the machine shares its last reading."""
    if override := os.environ.get("AEGIS_QUOTA_CACHE"):
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "aegis" / "quota"


def _retry_at(state: QuotaState, wall: float) -> int | None:
    return round(wall + state.retry_in_s) if state.retry_in_s > 0 else None


def account_hash(account: str | None) -> str | None:
    """Twelve hex digits of the account's SHA-256: enough to tell two accounts
    apart on one screen, nothing to recover the id from."""
    return hashlib.sha256(account.encode()).hexdigest()[:12] if account else None


def provider_wire(
    provider: QuotaProvider,
    state: QuotaState,
    *,
    now: datetime,
    wall: float,
    usual: Callable[[str, str, float, datetime], float] | None = None,
    project: Callable[
        [str, str, float, QuotaWindow, float, float], tuple[float, float] | None
    ]
    | None = None,
) -> dict | None:
    """One provider on the wire, or None when it has nothing to say: no
    credentials (a rail not in use), or no reading and no failure yet."""
    head = {
        "name": provider.name,
        "label": provider.label,
        # The sidebar shows the provider a session spends, matched on this.
        "harness": provider.harness,
        "account": account_hash(provider.account()),
    }
    note = FAILURE_TEXT.get(state.failure, state.failure)
    if state.snapshot is None:
        if not state.failure or state.failure == "no_credentials":
            return None
        return head | {
            "state": "failed",
            "note": note,
            "read_at": None,
            "retry_at": _retry_at(state, wall),
            "windows": [],
        }
    # A stale reading does not get to speak about pace: its percent is frozen
    # while the clock runs on, so the projection would fall on its own.
    stale = bool(state.failure)
    windows = []
    for kind, label in provider.bar_windows:
        w = state.snapshot.window(kind)
        if w is None:
            continue
        span = None if stale else provider.window_spans.get(kind)
        typical = PACE_USUAL_DEFAULT
        billing = f"{provider.name}:{head['account']}"
        got = None
        if span and head["account"] and w.resets_at is not None:
            # The current rate first (#1065); the window's average blended
            # with what finished windows spent while there is too little history.
            if project is not None:
                got = project(billing, kind, span, w, wall - state.age_s, wall)
            if usual is not None and got is None:
                typical = usual(billing, kind, span, w.resets_at)
        rate = None
        if got is not None:
            projected, rate = got
        else:
            projected = window_pace(w, span, now=now, usual=typical)
        resets = None if w.resets_at is None else w.resets_at.timestamp()
        starts = None
        if projected is not None and resets is not None and span:
            starts = round(resets - span)
        windows.append(
            {
                "kind": kind,
                "label": label,
                "percent": w.percent,
                "severity": "normal" if stale else severity_from(w, projected),
                "projected": None if projected is None else round(projected),
                # Points per hour, and which method made ``projected``.
                "rate": None if rate is None else round(rate, 2),
                "projection": None
                if projected is None
                else ("rate" if rate is not None else "average"),
                "starts_at": starts,
                "resets_at": None if resets is None else round(resets),
            }
        )
    return head | {
        "state": "stale" if stale else "ok",
        "note": note if stale else "",
        "read_at": round(wall - state.age_s),
        "retry_at": _retry_at(state, wall),
        "windows": windows,
    }


class Quota:
    def __init__(
        self,
        publish: Publish,
        *,
        providers: tuple[QuotaProvider, ...] = PROVIDERS,
        cache: Path | None = None,
        clock: Callable[[], float] = boot_clock,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._publish = publish
        self._providers = providers
        self._wall = wall
        self._clock = clock
        root = cache_dir() if cache is None else cache
        self.services = {
            p.name: QuotaService(
                fetch=p.fetch,
                token_reader=p.read_token,
                clock=clock,
                poll_s=p.poll_s,
                cache=root / f"{p.name}.json",
                wall=wall,
            )
            for p in providers
        }
        self._last: dict | None = None
        self._task: asyncio.Task | None = None
        self._nudges: set[asyncio.Task] = set()
        # Called with the snapshot after every check, changed or not: the
        # quota gate moves with the clock as pace does (quota_gate.py).
        self.listeners: list[Callable[[dict], None]] = []
        # Reads what a window's finished windows spent (Journal.usual_spend),
        # or None without any. Unset until the journal is there.
        self.history: Callable[[str, str, float], float | None] | None = None
        self._usual: dict[tuple[str, str], tuple[float, float]] = {}
        # Reads the usage_quota rows a projection replays (Journal.quota_rows),
        # and the quota: block's half-lives (config.quota_from). Unset, the
        # projection starts from the readings this process sees.
        self.rows: Callable[[str, str, float], list] | None = None
        self.half_lives: Callable[[], dict | str] = dict
        self._projector = Projector(
            lambda b, k, since: [] if self.rows is None else self.rows(b, k, since)
        )

    def _project(
        self,
        billing: str,
        kind: str,
        span: float,
        w: QuotaWindow,
        at: float,
        wall: float,
    ) -> tuple[float, float] | None:
        hl = half_life(span, self.half_lives())
        if hl is None or w.resets_at is None:
            return None
        return self._projector.project(
            billing, kind, span, hl, at, w.percent, w.resets_at.timestamp(), wall
        )

    def _usual_spend(
        self, billing: str, kind: str, span: float, resets_at: datetime
    ) -> float:
        """What this window usually spends, read once per window: it only
        changes when one finishes, and a snapshot is taken every minute. A
        vendor's reset time moves by a second between readings."""
        resets = resets_at.timestamp()
        known = self._usual.get((billing, kind))
        if known is not None and abs(known[0] - resets) < 60:
            return known[1]
        got = None if self.history is None else self.history(billing, kind, span)
        typical = PACE_USUAL_DEFAULT if got is None else got
        if self.history is not None:
            self._usual[billing, kind] = (resets, typical)
        return typical

    def snapshot(self) -> dict:
        wall = self._wall()
        now = datetime.fromtimestamp(wall, timezone.utc)
        providers = []
        for p in self._providers:
            wire = provider_wire(
                p,
                self.services[p.name].current(),
                now=now,
                wall=wall,
                usual=self._usual_spend,
                project=self._project,
            )
            if wire is not None:
                providers.append(wire)
        return {"providers": providers}

    def check(self) -> None:
        snap = self.snapshot()
        if snap != self._last:
            self._last = snap
            self._publish("quota", [{"set": snap}])
        for listener in list(self.listeners):
            try:
                listener(snap)
            except Exception:  # a listener must never stop the loop
                log.exception("quota listener failed")

    async def tick(self) -> None:
        for p in self._providers:
            await self.services[p.name].refresh()
        self.check()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:  # a tick must never end the loop
                log.exception("quota tick failed")
            await asyncio.sleep(TICK_S)

    async def stop(self) -> None:
        task, self._task = self._task, None
        for t in list(self._nudges):
            await cancel_and_wait(t)
        await cancel_and_wait(task)

    def observe(self, windows: dict) -> None:
        """A Claude session's stream reported the account's windows."""
        snap = parse_stream(windows, now=self._clock())
        if snap is None:
            return
        self.services[CLAUDE.name].observe(snap)
        self.check()

    def turn_ended(self) -> None:
        """A Claude turn just spent quota: refresh Claude before its cadence
        would, but no sooner than its turn floor after the last fetch."""
        if self._task is None:
            return
        for p in self._providers:
            if p.name == CLAUDE.name:
                t = asyncio.create_task(self._nudge(p))
                self._nudges.add(t)
                t.add_done_callback(self._nudges.discard)

    async def _nudge(self, p: QuotaProvider) -> None:
        await self.services[p.name].refresh(min_interval=p.turn_floor_s)
        self.check()
