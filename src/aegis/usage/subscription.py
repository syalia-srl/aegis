"""A subscription's fee and limit, split over what ran on it (spec 08).

A rise of the window's percent between two readings goes to the groups that
ran in the hours that interval covers, by their cost at API prices; a rise
with no cost behind it is elsewhere (the provider's own apps, a machine
aegis does not run on). At a reset, what the window had not used is unused.
100 points of the window equal one full period's fee (a week of the monthly
fee for Claude and OpenCode Go; the monthly fee itself for Cursor's Auto
pool, #1134). A slice's dollars are that period fee times the points it used
over 100, so the slices, elsewhere and unused add up to the fee. Hours
before the first reading (history, imports, a harness with no quota
provider) are apportioned: that stretch's fee split by cost. Costs come by
hour because a turn's parts are grouped by hour on every server, which is
what a merge across servers can share.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

WEEK = 7 * 86400
MONTH = 31 * 86400  # longest month; matches quota/cursor.MONTH_S
HOUR = 3600
# Provider name (billing before the colon) -> (quota window kind, seconds in
# one full period whose fee 100 points buy). Claude and OpenCode Go meter a
# weekly window; Cursor meters Auto monthly (#1134). API-pool-only rises on
# Cursor with no Auto move stay elsewhere for now.
WINDOWS: dict[str, tuple[str, int]] = {
    "claude": ("weekly_all", WEEK),
    "opencode-go": ("weekly", WEEK),
    "cursor": ("auto", MONTH),
}
# Backward name: window kind only, for callers that do not need the period.
WEEKLY = {name: kind for name, (kind, _) in WINDOWS.items()}


@dataclass(frozen=True)
class Reading:
    ts: float
    percent: float
    resets_at: float | None


@dataclass
class SubSplit:
    limit: dict[tuple, float]  # points of the window, per group
    dollars: dict[tuple, Decimal]  # subscription cost, per group
    elsewhere: float  # points used with no turn behind them
    unused: float  # points left at a reset
    kind: str  # metered or apportioned
    apportioned: Decimal = Decimal(0)  # dollars split by cost, without readings


def period_fee(fee_month: Decimal, period_s: float) -> Decimal:
    """USD that 100 points of the window buy: a week of the monthly fee, or
    the monthly fee when the window is a billing month."""
    if period_s <= WEEK:
        return fee_month * 12 / 52
    return fee_month


def weekly_fee(fee_month: Decimal) -> Decimal:
    """A week of the monthly fee (Claude / OpenCode Go windows)."""
    return period_fee(fee_month, WEEK)


def _apportion(hours: dict, fee: Decimal, into: dict) -> None:
    by: dict[tuple, Decimal] = defaultdict(Decimal)
    for (_, key), cost in hours.items():
        by[key] += cost
    total = sum(by.values(), Decimal(0))
    if total:
        for key, cost in by.items():
            into[key] = into.get(key, Decimal(0)) + fee * cost / total


def split(
    readings: list[Reading],
    hourly: dict[tuple[int, tuple], Decimal],
    fee_month: Decimal | None,
    span: tuple[float, float] | None = None,
    *,
    period_s: float = WEEK,
) -> SubSplit:
    """Share the readings' rises over ``hourly`` (hour start, group) -> cost
    at API prices. ``span`` is the period asked, whose fee is apportioned
    where there are no readings; by default the hours' own span.
    ``period_s`` is how long 100 points of the window last (a week or a
    month)."""
    unit = period_fee(fee_month, period_s) if fee_month is not None else None
    readings = sorted(readings, key=lambda r: r.ts)
    first = readings[0].ts if readings else float("inf")
    before = {k: c for k, c in hourly.items() if k[0] + HOUR <= first}
    dollars: dict[tuple, Decimal] = {}
    apportioned = Decimal(0)
    if before and unit is not None:
        since = span[0] if span else min(h for h, _ in before)
        until = (
            min(first, span[1])
            if span
            else min(first, max(h for h, _ in before) + HOUR)
        )
        apportioned = unit * Decimal(str(max(until - since, 0))) / Decimal(period_s)
        _apportion(before, apportioned, dollars)
    if not readings:
        return SubSplit({}, dollars, 0.0, 0.0, "apportioned", apportioned)
    by_hour: dict[int, list[tuple[tuple, Decimal]]] = defaultdict(list)
    for (h, key), cost in hourly.items():
        by_hour[int(h)].append((key, cost))
    limit: dict[tuple, Decimal] = defaultdict(Decimal)
    elsewhere = unused = Decimal(0)
    for a, b in zip(readings, readings[1:]):
        pa, pb = Decimal(str(a.percent)), Decimal(str(b.percent))
        reset = pb < pa or (b.resets_at or 0) - (a.resets_at or 0) > HOUR
        if reset:
            unused += max(Decimal(100) - pa, Decimal(0))
        rise = pb if reset else pb - pa
        if rise <= 0:
            continue
        ran: dict[tuple, Decimal] = defaultdict(Decimal)
        h = int(a.ts // HOUR * HOUR)
        while h < b.ts:
            for key, cost in by_hour.get(h, ()):
                ran[key] += cost
            h += HOUR
        total = sum(ran.values(), Decimal(0))
        if not total:
            elsewhere += rise
            continue
        for key, cost in ran.items():
            limit[key] += rise * cost / total
    if unit is not None:
        for key, points in limit.items():
            dollars[key] = dollars.get(key, Decimal(0)) + unit * points / 100
    return SubSplit(
        {k: float(v) for k, v in limit.items()},
        dollars,
        float(elsewhere),
        float(unused),
        "metered",
        apportioned,
    )
