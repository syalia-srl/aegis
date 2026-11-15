"""The projection at the current rate (#1065): spent plus rate times hours left,
the rate an exponentially weighted average of the percent's rise."""

import pytest

from aegis.quota.projection import Projector, half_life

H = 3600.0
WK = 7 * 86400.0
HL = 6 * H
T0 = 1_000_000_000.0
RESET = T0 + WK  # the window that opens at T0


def _feed(p, readings, *, resets=RESET, billing="claude:abc", kind="weekly_all"):
    """Readings are (hours after T0, percent); returns the last projection."""
    out = None
    for h, pct in readings:
        at = T0 + h * H
        out = p.project(billing, kind, WK, HL, at, pct, resets, at)
    return out


def test_steady_spend_projects_at_that_rate():
    # One point an hour for two days: the rate is 1 %/h.
    got = _feed(Projector(), [(h, float(h)) for h in range(0, 49)])
    projected, rate = got
    assert rate == pytest.approx(1.0, rel=0.01)
    assert projected == pytest.approx(48 + 1.0 * (168 - 48), rel=0.01)


def test_a_stop_halves_the_excess_each_half_life():
    p = Projector()
    _, before = _feed(p, [(h, float(h)) for h in range(0, 31)])
    at = T0 + (30 + 6) * H  # one half-life after the last rise, nothing spent
    projected, rate = p.project(
        "claude:abc", "weekly_all", WK, HL, T0 + 30 * H, 30.0, RESET, at
    )
    assert rate == pytest.approx(before / 2)
    assert projected == pytest.approx(30 + before / 2 * (168 - 36))


def test_less_than_one_half_life_of_history_declines():
    assert _feed(Projector(), [(0, 0.0), (1, 1.0), (5, 5.0)]) is None


def test_alternating_readings_count_one_rise():
    p = Projector()
    _feed(p, [(h, float(h)) for h in range(0, 10)])
    a = _feed(p, [(10, 10.0), (10.1, 9.0), (10.2, 10.0), (11, 11.0)])
    b = _feed(
        Projector(), [(h, float(h)) for h in range(0, 10)] + [(10, 10.0), (11, 11.0)]
    )
    assert a[1] == pytest.approx(b[1], rel=0.05)
    assert a[0] >= 11 + a[1] * (168 - 11) - 1e-6


def test_the_rate_carries_across_a_reset():
    p = Projector()
    _feed(p, [(h, float(h)) for h in range(0, 30)])  # 1 %/h, last reading at hour 29
    # The window ends at hour 30 and the next one opens; its first reading is 1%.
    nxt = RESET + WK
    at = T0 + 30 * H
    projected, rate = p.project("claude:abc", "weekly_all", WK, HL, at, 1.0, nxt, at)
    assert rate == pytest.approx(1.0, rel=0.1)  # not zero, not reset
    assert projected == pytest.approx(1 + rate * (nxt - at) / H, rel=0.01)


def test_a_reset_detected_when_the_old_window_ended():
    # OpenCode's rolling window: after it ends, the next reset is close to the old one.
    p = Projector()
    span = 5 * H
    hl = 1800.0
    first_reset = T0 + span
    for m in range(0, 300, 10):
        at = T0 + m * 60
        p.project("og:x", "rolling", span, hl, at, m / 30, first_reset, at)
    after = first_reset + 600  # the window is over, a new one starts at 1%
    projected, rate = p.project(
        "og:x", "rolling", span, hl, after, 1.0, after + span, after
    )
    assert projected >= 1.0 and rate > 0


def test_seed_from_rows_equals_live_observation():
    rows = [(T0 + h * H, float(h // 2), RESET) for h in range(0, 40, 2)]
    live = Projector()
    for at, pct, res in rows:
        live.observe("claude:abc", "weekly_all", WK, HL, at, pct, res, at)
    now = rows[-1][0]
    seeded = Projector(lambda b, k, since: [r for r in rows if r[0] >= since - 1e9])
    a = live.project("claude:abc", "weekly_all", WK, HL, *rows[-1], now)
    b = seeded.project("claude:abc", "weekly_all", WK, HL, *rows[-1], now)
    assert a == pytest.approx(b)


def test_a_seed_asks_for_seven_half_lives():
    asked = []
    p = Projector(lambda b, k, since: asked.append(since) or [])
    p.project("claude:abc", "weekly_all", WK, HL, T0, 1.0, RESET, T0)
    assert asked == [T0 - 7 * HL]


@pytest.mark.parametrize(
    ("span", "config", "want"),
    [
        (WK, None, 6 * H),
        (5 * H, None, 1800.0),
        (30 * 86400.0, None, 30 * 86400 / 28),
        (WK, {"week": 24 * H}, 24 * H),
        (WK, {"5h": 600.0}, 6 * H),
        (WK, "average", None),
    ],
)
def test_half_life_per_window_length(span, config, want):
    assert half_life(span, config) == want
