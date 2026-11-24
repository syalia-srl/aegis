import asyncio
import dataclasses
import json
from datetime import datetime, timezone

import pytest

from aegis.quota import Quota, CODEX
from aegis.quota.codex import read_token, account_id, parse_limits, fetch_usage
from aegis.quota.core import QuotaError


def test_auth_metadata_does_not_copy_the_oauth_token(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    monkeypatch.setenv("CODEX_AUTH", str(path))
    monkeypatch.setattr("aegis.quota.codex.shutil.which", lambda _: "/bin/codex")
    assert read_token() is None
    path.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "tokens": {"account_id": "account-1", "access_token": "private"},
            }
        )
    )
    assert read_token() == "/bin/codex"
    assert account_id() == "account-1"
    path.write_text('{"auth_mode":"apikey","OPENAI_API_KEY":"private"}')
    assert read_token() is None and account_id() is None
    path.write_text("[]")
    assert read_token() is None


def test_primary_can_be_the_weekly_window_and_multibucket_codex_wins():
    payload = {
        "rateLimits": {"primary": {"usedPercent": 99}},
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {
                    "usedPercent": 12,
                    "windowDurationMins": 10080,
                    "resetsAt": 1800000000,
                },
                "secondary": None,
            }
        },
    }
    snap = parse_limits(payload, now=123)
    (week,) = snap.windows
    assert week.kind == "weekly_all" and week.percent == 12
    assert week.span_s == 604800
    assert week.resets_at == datetime.fromtimestamp(1800000000, timezone.utc)
    assert snap.fetched_at == 123


def test_five_hours_and_unknown_duration_keep_their_actual_spans():
    snap = parse_limits(
        {
            "rateLimits": {
                "primary": {"usedPercent": 25, "windowDurationMins": 300},
                "secondary": {"usedPercent": 85, "windowDurationMins": 15},
            }
        },
        now=1,
    )
    assert [(w.kind, w.span_s) for w in snap.windows] == [
        ("session", 18000),
        ("secondary", 900),
    ]
    assert snap.windows[1].severity == "warning"


@pytest.mark.parametrize("value", [True, -1, 101, float("nan"), "12", None])
def test_malformed_usage_does_not_become_a_zero_percent_gauge(value):
    with pytest.raises(QuotaError, match="unreachable"):
        parse_limits({"rateLimits": {"primary": {"usedPercent": value}}}, now=1)


async def test_codex_rpc_populates_the_real_quota_wire(tmp_path, fake_codex):
    provider = dataclasses.replace(
        CODEX, read_token=lambda: fake_codex, account=lambda: "account-1"
    )
    quota = Quota(lambda *_: None, providers=(provider,), cache=tmp_path / "cache")
    await quota.tick()
    (got,) = quota.snapshot()["providers"]
    assert got["name"] == "codex" and got["harness"] == "codex" and got["state"] == "ok"
    assert got["account"] != "account-1"
    (week,) = got["windows"]
    assert week["kind"] == "weekly_all" and week["percent"] == 12
    log = tmp_path / "fake-codex.log"
    await asyncio.to_thread(fetch_usage, fake_codex)
    methods = log.read_text()
    assert "account/rateLimits/read" in methods
    assert "thread/start" not in methods and "turn/start" not in methods


async def test_an_unauthenticated_rpc_is_an_explicit_failure(fake_codex, monkeypatch):
    monkeypatch.setenv(
        "FAKE_CODEX_QUOTA_ERROR",
        "codex account authentication required to read rate limits",
    )
    with pytest.raises(QuotaError, match="unauthorized"):
        await asyncio.to_thread(fetch_usage, fake_codex)
