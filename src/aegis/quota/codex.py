"""ChatGPT subscription quota through Codex's authenticated app-server.

No OAuth token is copied or refreshed here: Codex owns authentication. A short
probe asks account/rateLimits/read without opening a thread or calling a model.
The local auth file identifies a ChatGPT login and its account; only its hash
reaches the wire. The returned window duration, not primary/secondary position,
determines whether a bucket is the five-hour or weekly allowance (#105).
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .core import (
    QuotaError,
    QuotaProvider,
    QuotaSnapshot,
    QuotaWindow,
    _severity,
    boot_clock,
)


def _auth(path: Path | None = None) -> dict:
    if path is None:
        path = (
            Path(os.environ["CODEX_AUTH"])
            if os.environ.get("CODEX_AUTH")
            else Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
            / "auth.json"
        )
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_token(path: Path | None = None) -> str | None:
    """A probe executable, not a token. API-key logins have no plan quota."""
    data = _auth(path)
    if data.get("auth_mode") not in ("chatgpt", "chatgptAuthTokens"):
        return None
    return shutil.which("codex")


def account_id(path: Path | None = None) -> str | None:
    data = _auth(path)
    if data.get("auth_mode") not in ("chatgpt", "chatgptAuthTokens"):
        return None
    tokens = data.get("tokens")
    value = tokens.get("account_id") if isinstance(tokens, dict) else None
    return value if isinstance(value, str) and value else None


def parse_limits(payload: dict, *, now: float) -> QuotaSnapshot:
    """Read Codex's bucket, retaining the actual span and Unix-second reset.

    New servers carry multiple buckets; prefer codex over the legacy single
    bucket. Do not combine unrelated model allowances into one percentage.
    """
    by_id = payload.get("rateLimitsByLimitId")
    limits = by_id.get("codex") if isinstance(by_id, dict) else None
    if not isinstance(limits, dict):
        limits = payload.get("rateLimits")
    if not isinstance(limits, dict):
        raise QuotaError("unreachable")
    windows = []
    seen = set()
    for slot in ("primary", "secondary"):
        raw = limits.get(slot)
        if not isinstance(raw, dict):
            continue
        percent, mins, reset = (
            raw.get(k) for k in ("usedPercent", "windowDurationMins", "resetsAt")
        )
        if (
            isinstance(percent, bool)
            or not isinstance(percent, (int, float))
            or not math.isfinite(percent)
            or not 0 <= percent <= 100
        ):
            continue
        span = (
            float(mins) * 60
            if isinstance(mins, (int, float))
            and not isinstance(mins, bool)
            and math.isfinite(mins)
            and mins > 0
            else None
        )
        kind = "session" if span == 18000 else "weekly_all" if span == 604800 else slot
        if kind in seen:
            continue
        resets_at = None
        if (
            isinstance(reset, (int, float))
            and not isinstance(reset, bool)
            and math.isfinite(reset)
        ):
            try:
                resets_at = datetime.fromtimestamp(reset, timezone.utc)
            except (ValueError, OverflowError, OSError):
                pass
        windows.append(
            QuotaWindow(
                kind, float(percent), _severity(percent, None), resets_at, True, span
            )
        )
        seen.add(kind)
    if not windows:
        raise QuotaError("unreachable")
    return QuotaSnapshot(tuple(windows), now)


async def _read_limits(bin: str, timeout: float) -> dict:
    from .. import launch
    from ..codex.process import CodexProcess
    from ..harness import Launch

    with tempfile.TemporaryDirectory(prefix="aegis-codex-quota-") as folder:
        root = Path(folder)
        process = CodexProcess(
            bin,
            Launch(
                cwd=root,
                model="",
                effort="",
                permission="read",
                resume_id=None,
                mcp=None,
                system_prompt=None,
                stderr_path=root / "stderr",
                via=launch.resolve_via(),
                on_line=lambda _: None,
                on_exit=lambda *_: None,
            ),
        )
        try:
            await process._spawn()
            return await process._call("account/rateLimits/read", {}, timeout)
        finally:
            await process.terminate()


def fetch_usage(
    bin: str, *, timeout: float = 10, now: float | None = None
) -> QuotaSnapshot:
    from ..codex.process import RpcError

    try:
        payload = asyncio.run(_read_limits(bin, timeout))
    except RpcError as exc:
        message = str(exc).lower()
        kind = (
            "unauthorized"
            if any(s in message for s in ("auth", "login", "sign in"))
            else "rate_limited"
            if "429" in message
            else "unreachable"
        )
        raise QuotaError(kind) from None
    except (OSError, TimeoutError, ConnectionError, ValueError, TypeError):
        raise QuotaError("unreachable") from None
    return parse_limits(payload, now=boot_clock() if now is None else now)


PROVIDER = QuotaProvider(
    name="codex",
    label="ChatGPT",
    harness="codex",
    bar_windows=(
        ("session", "5 hours"),
        ("weekly_all", "week"),
        ("primary", "primary"),
        ("secondary", "secondary"),
    ),
    fetch=fetch_usage,
    read_token=read_token,
    account=account_id,
    poll_s=60,
    turn_floor_s=30,
)
