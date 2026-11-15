"""The quota: block of .aegis.yaml and the rows a projection replays (#1065)."""

import sqlite3

import pytest

from aegis.config import quota_from
from aegis.usage import tables


def test_no_block_means_defaults():
    assert quota_from(None) == {"half_life": {}}
    assert quota_from({"projection": None}) == {"half_life": {}}


def test_half_lives_parse_to_seconds():
    got = quota_from(
        {"projection": {"half_life": {"5h": "30m", "week": "6h", "month": "1.5d"}}}
    )
    assert got == {"half_life": {"5h": 1800.0, "week": 21600.0, "month": 129600.0}}


def test_average_keeps_the_whole_window_method():
    assert quota_from({"projection": {"half_life": "average"}}) == {
        "half_life": "average"
    }


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        ("yes", "quota takes one key"),
        ({"pace": 1}, "quota takes one key"),
        ({"projection": {"window": 1}}, "quota.projection takes one key"),
        ({"projection": {"half_life": 6}}, "must be average or a mapping"),
        ({"projection": {"half_life": {"day": "6h"}}}, "has no key 'day'"),
        ({"projection": {"half_life": {"week": "6"}}}, "is not a duration"),
        ({"projection": {"half_life": {"5h": "6h"}}}, "is not a duration"),
        ({"projection": {"half_life": {"week": "5m"}}}, "is not a duration"),
    ],
)
def test_a_wrong_block_keeps_defaults_and_says_why(raw, says):
    got = quota_from(raw)
    assert got["half_life"] == {} and says in got["error"]


def test_quota_rows_lead_with_the_state_before_since():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE usage_quota(billing TEXT, window TEXT, ts REAL, percent REAL, resets_at REAL)"
    )
    rows = [("c:a", "weekly_all", float(t), float(t), 999.0) for t in (10, 20, 30, 40)]
    con.executemany("INSERT INTO usage_quota VALUES(?,?,?,?,?)", rows)
    con.execute("INSERT INTO usage_quota VALUES('c:a','session',25,1,999)")
    assert tables.quota_rows(con, "c:a", "weekly_all", 25.0) == [
        (20.0, 20.0, 999.0),
        (30.0, 30.0, 999.0),
        (40.0, 40.0, 999.0),
    ]
    assert tables.quota_rows(con, "c:b", "weekly_all", 0.0) == []


def test_quota_is_a_known_top_level_key():
    from aegis.config import TOP_KEYS

    assert "quota" in TOP_KEYS
