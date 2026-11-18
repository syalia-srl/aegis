import base64
import json
import sqlite3
import urllib.error
from datetime import timezone

import pytest

from aegis.quota.core import QuotaError
from aegis.quota.cursor import account_id, fetch_usage, parse_usage, read_token

# Captured from a live POST on 2026-11-01, trimmed to the fields the parser
# reads. The dollar bucket (includedSpend 1457 of limit 2000) is about 73% and
# is not one of the two pools; totalPercentUsed is not either.
LIVE = {
    "billingCycleStart": "1792191000000",
    "billingCycleEnd": "1794869400000",
    "planUsage": {
        "totalSpend": 1457,
        "includedSpend": 1457,
        "remaining": 543,
        "limit": 2000,
        "autoPercentUsed": 2.7911111111111113,
        "apiPercentUsed": 4.466666666666667,
        "totalPercentUsed": 2.9434343434343435,
    },
    "displayMessage": "You've used 73% of your included usage",
}


def _jwt(sub: str) -> str:
    def part(obj) -> str:
        raw = base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=")
        return raw.decode()

    return f"{part({'alg': 'none'})}.{part({'sub': sub})}.sig"


def test_read_token_returns_none_when_missing(tmp_path):
    assert read_token(tmp_path / "nope.json") is None


def test_read_token_returns_none_on_garbage(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text("not json{")
    assert read_token(p) is None


def test_read_token_reads_the_access_token(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"accessToken": "tok", "refreshToken": "r"}))
    assert read_token(p) == "tok"


def test_read_token_falls_back_to_the_editor_when_the_cli_file_is_absent(
    tmp_path, monkeypatch
):
    import aegis.quota.cursor as cursor

    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    con.execute(
        "INSERT INTO ItemTable VALUES (?, ?)",
        ("cursorAuth/accessToken", "ide-tok"),
    )
    con.commit()
    con.close()
    monkeypatch.setattr(cursor, "auth_path", lambda: tmp_path / "missing.json")
    monkeypatch.setattr(cursor, "_ide_db", lambda: db)
    assert read_token() == "ide-tok"


def test_an_explicit_path_does_not_fall_through_to_the_editor(tmp_path):
    assert read_token(tmp_path / "missing.json") is None


def test_account_id_is_the_token_subject(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"accessToken": _jwt("user_123")}))
    assert account_id(p) == "user_123"


def test_account_id_is_none_for_a_token_that_is_not_a_jwt(tmp_path):
    p = tmp_path / "auth.json"
    p.write_text(json.dumps({"accessToken": "not-a-jwt"}))
    assert account_id(p) is None


def test_parse_reads_the_two_pools():
    snap = parse_usage(LIVE, now=100.0)
    assert [w.kind for w in snap.windows] == ["auto", "api"]
    assert snap.window("auto").percent == pytest.approx(2.7911111111111113)
    assert snap.window("api").percent == pytest.approx(4.466666666666667)
    assert snap.fetched_at == 100.0


def test_parse_ignores_the_dollar_bucket_and_the_combined_percent():
    # includedSpend/limit is ~73%, and totalPercentUsed is a third number.
    # Neither is a window: the plan has two pools.
    snap = parse_usage(LIVE, now=0.0)
    assert [w.kind for w in snap.windows] == ["auto", "api"]


def test_parse_shares_one_billing_cycle_across_both_pools():
    snap = parse_usage(LIVE, now=0.0)
    end = snap.window("auto").resets_at
    assert end == snap.window("api").resets_at
    assert end.tzinfo is not None
    assert end.astimezone(timezone.utc).timestamp() == pytest.approx(1794869400)
    assert snap.window("auto").span_s == 31 * 86400
    assert snap.window("api").span_s == snap.window("auto").span_s


def test_parse_treats_both_pools_as_active():
    assert all(w.is_active for w in parse_usage(LIVE, now=0.0).windows)


def test_parse_derives_severity_from_percent():
    payload = {
        "billingCycleEnd": "1794869400000",
        "planUsage": {"autoPercentUsed": 50, "apiPercentUsed": 91},
    }
    sev = {w.kind: w.severity for w in parse_usage(payload, now=0.0).windows}
    assert sev == {"auto": "normal", "api": "critical"}


def test_parse_drops_an_unreadable_pool_without_failing():
    payload = {"planUsage": {"autoPercentUsed": 12, "apiPercentUsed": None}}
    assert [w.kind for w in parse_usage(payload, now=0.0).windows] == ["auto"]


def test_parse_survives_a_missing_usage_object():
    assert parse_usage({}, now=0.0).windows == ()


def test_parse_leaves_the_span_unset_without_a_cycle():
    snap = parse_usage({"planUsage": {"autoPercentUsed": 1}}, now=0.0)
    assert snap.window("auto").resets_at is None
    assert snap.window("auto").span_s is None


class _Resp:
    def __init__(self, body):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_fetch_posts_the_two_headers_the_endpoint_requires():
    seen = {}

    def opener(req, timeout=None):
        seen["method"] = req.get_method()
        seen["data"] = req.data
        seen["auth"] = req.get_header("Authorization")
        seen["ctype"] = req.get_header("Content-type")
        seen["connect"] = req.get_header("Connect-protocol-version")
        return _Resp(json.dumps(LIVE).encode())

    snap = fetch_usage("tok", opener=opener, now=5.0)
    assert seen == {
        "method": "POST",
        "data": b"{}",
        "auth": "Bearer tok",
        "ctype": "application/json",
        "connect": "1",
    }
    assert snap.window("api").percent == pytest.approx(4.466666666666667)


def test_fetch_maps_401_to_unauthorized():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 401, "no", {}, None)

    with pytest.raises(QuotaError) as e:
        fetch_usage("tok", opener=opener)
    assert e.value.kind == "unauthorized"


def test_fetch_maps_429_to_rate_limited():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 429, "slow down", {}, None)

    with pytest.raises(QuotaError) as e:
        fetch_usage("tok", opener=opener)
    assert e.value.kind == "rate_limited"


def test_fetch_maps_500_to_unreachable():
    def opener(req, timeout=None):
        raise urllib.error.HTTPError("u", 500, "boom", {}, None)

    with pytest.raises(QuotaError) as e:
        fetch_usage("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_fetch_maps_truncated_body_to_unreachable():
    def opener(req, timeout=None):
        return _Resp(b'{"planUsage": {')

    with pytest.raises(QuotaError) as e:
        fetch_usage("tok", opener=opener)
    assert e.value.kind == "unreachable"


def test_a_fetch_stamps_its_reading_on_the_clock_that_counts_sleep(monkeypatch):
    import aegis.quota.cursor as cursor

    monkeypatch.setattr(cursor, "boot_clock", lambda: 42.0)
    snap = fetch_usage(
        "tok", opener=lambda req, timeout=None: _Resp(json.dumps(LIVE).encode())
    )
    assert snap.fetched_at == 42.0
