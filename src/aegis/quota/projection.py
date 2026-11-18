"""Where a quota window ends at the current spending rate (#1065).

``projected = percent + rate * hours_left``. What is spent is the vendor's
reading; only the rest is estimated, from ``rate``: an exponentially weighted
average of how fast the percent rises, in points per hour, with a half-life set
per window length (``quota.projection.half_life`` in ``.aegis.yaml``).

The rate is kept per account and window kind and carries across resets: it
describes the work, which does not reset with the window. It moves only when a
reading changes the percent or the reset time, the same rows the journal keeps
in ``usage_quota``, so replaying that table at start gives the rate a live
server would hold. Between changes, time without a rise counts as time without
spending, applied when projecting.

Readings are whole percents and two readings minutes apart can differ by one
point (36, 37, 36), so inside a window the percent is a running maximum.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

# A window length's name in .aegis.yaml, by its span in seconds.
SPAN_NAMES = {5 * 3600: "5h", 7 * 86400: "week", 30 * 86400: "month"}
# A window whose length has no half-life set uses its span over this, but never
# less than MIN_HALF_LIFE_S: a week gets 6 h, five hours 30 min, a month 26 h.
DEFAULT_SPAN_RATIO = 28
MIN_HALF_LIFE_S = 1800.0
# How many half-lives of history a seed replays: older rows weigh under 1%.
SEED_HALF_LIVES = 7

Rows = Callable[[str, str, float], list[tuple[float, float, float | None]]]


def half_life(span_s: float, config: dict | str | None) -> float | None:
    """The half-life in seconds for a window of this span, or None when the
    configuration asks for the whole-window average."""
    if config == "average":
        return None
    name = SPAN_NAMES.get(round(span_s))
    if isinstance(config, dict) and name in config:
        return float(config[name])
    return max(MIN_HALF_LIFE_S, span_s / DEFAULT_SPAN_RATIO)


@dataclass
class _Rate:
    half_life: float
    first: float  # time of the oldest row replayed
    at: float  # time of the last change
    rate: float  # points per hour at ``at``
    peak: float  # running maximum of the percent in the current window
    resets: float | None


def _same_window(r: _Rate, at: float, resets: float | None, span_s: float) -> bool:
    """A reading belongs to the window ``r`` is in unless that window ended
    before it, or its reset time jumped by half a span. OpenCode's idle rolling
    window moves its reset with the clock, which is neither."""
    if r.resets is None or resets is None:
        return r.resets == resets
    return at < r.resets and abs(resets - r.resets) < span_s / 2


class Projector:
    """The rate per (billing, window kind), seeded once from the journal."""

    def __init__(self, rows: Rows | None = None) -> None:
        self._rows = rows
        self._rates: dict[tuple[str, str], _Rate] = {}

    def _update(self, r: _Rate, at: float, percent: float, resets, span_s) -> None:
        if at <= r.at:
            return
        if _same_window(r, at, resets, span_s):
            rise = max(0.0, percent - r.peak)
            if rise == 0 and resets == r.resets:
                return  # nothing moved: no change to keep
            r.peak = max(r.peak, percent)
        else:
            rise, r.peak = percent, percent
        r.resets = resets
        hours = (at - r.at) / 3600
        a = 0.5 ** ((at - r.at) / r.half_life)
        r.rate = a * r.rate + (1 - a) * rise / hours
        r.at = at

    def _seed(
        self, billing: str, kind: str, hl: float, now: float, span_s
    ) -> _Rate | None:
        rows = (
            []
            if self._rows is None
            else self._rows(billing, kind, now - SEED_HALF_LIVES * hl)
        )
        rows = sorted(rows)
        if not rows:
            return None
        at, percent, resets = rows[0]
        r = _Rate(hl, at, at, 0.0, percent, resets)
        for at, percent, resets in rows[1:]:
            self._update(r, at, percent, resets, span_s)
        return r

    def observe(
        self,
        billing: str,
        kind: str,
        span_s: float,
        hl: float,
        at: float,
        percent: float,
        resets: float | None,
        now: float,
    ) -> _Rate:
        """Bring the rate up to this reading, seeding it first when needed."""
        key = (billing, kind)
        r = self._rates.get(key)
        if r is None or r.half_life != hl:
            r = self._seed(billing, kind, hl, now, span_s) or _Rate(
                hl, at, at, 0.0, percent, resets
            )
            self._rates[key] = r
        self._update(r, at, percent, resets, span_s)
        return r

    def project(
        self,
        billing: str,
        kind: str,
        span_s: float,
        hl: float,
        at: float,
        percent: float,
        resets: float | None,
        now: float,
    ) -> tuple[float, float] | None:
        """``(projected, rate)`` for this reading, or None while the history
        covers less than one half-life (the caller falls back)."""
        if resets is None:
            return None
        r = self.observe(billing, kind, span_s, hl, at, percent, resets, now)
        if now - r.first < hl:
            return None
        rate = r.rate * 0.5 ** (max(0.0, now - r.at) / hl)
        spent = max(percent, r.peak) if _same_window(r, at, resets, span_s) else percent
        left_h = max(0.0, resets - now) / 3600
        return spent + rate * left_h, rate
