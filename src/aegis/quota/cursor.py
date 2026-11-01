"""Live Cursor subscription quota — the Auto and API pools.

Cursor's plan has two included pools, both resetting with the billing month
(measured 2026-11-01 against ``GetCurrentPeriodUsage``): Auto, which
first-party models draw on, and API, which named models draw on. The payload
also carries a dollar ``limit`` and a ``totalPercentUsed``. Those are not a
third window. On that day the dollar bucket read about 73% while Auto was
about 3% and API about 4%, and the dashboard's own sentences name the two
pools ("included total usage", "included API usage").

The agent CLI stores an access token at ``~/.config/cursor/auth.json``. The
editor stores another, sometimes for a different account, in its state
database. This reads the CLI token, because that is the account
``cursor-agent`` spends, and the editor's token only when the CLI has none.

The endpoint is undocumented and may change or vanish. Every failure path here
degrades to a note on the gauges; nothing raises into the caller.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .core import (
    boot_clock,
    QuotaError,
    QuotaProvider,
    QuotaSnapshot,
    QuotaWindow,
    _severity,
)

URL = "https://api2.cursor.sh/aiserver.v1.DashboardService/GetCurrentPeriodUsage"

# The two pools the gauges draw, in the order the dashboard names them. Any
# other percent in the payload is ignored rather than guessed at.
WINDOW_KINDS = (("auto", "autoPercentUsed"), ("api", "apiPercentUsed"))

# Longest month. Used only when a reading has no cycle of its own, so a pace
# line can still be offered. A real cycle replaces it (core.QuotaWindow.span_s).
MONTH_S = 31 * 86400


def auth_path() -> Path:
    """Where the Cursor agent CLI keeps its access token. ``CURSOR_AUTH``
    overrides."""
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    default = Path(base) / "cursor" / "auth.json"
    return Path(os.environ.get("CURSOR_AUTH", str(default)))


def _ide_db() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _token_from_file(path: Path) -> str | None:
    try:
        with path.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("accessToken")
    return token if isinstance(token, str) and token else None


def _token_from_ide() -> str | None:
    """The editor's access token, or None. Never raises.

    The state database is large. This opens it read-only and reads one row,
    and only when the CLI file has no token.
    """
    db = _ide_db()
    if not db.is_file():
        return None
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return None
    if not row or not isinstance(row[0], str) or not row[0]:
        return None
    return row[0]


def read_token(path: Path | None = None) -> str | None:
    """The access token, or None if there isn't a usable one.

    Never raises: a missing file, unreadable JSON and an absent field are all
    the same answer — we cannot ask about quota. An explicit ``path`` does not
    fall through to the editor's database.
    """
    token = _token_from_file(path or auth_path())
    if token or path is not None:
        return token
    return _token_from_ide()


def _jwt_sub(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        pad = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(pad))
    except (IndexError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sub = data.get("sub")
    return sub if isinstance(sub, str) and sub else None


def account_id(path: Path | None = None) -> str | None:
    """The signed-in account's id, from the token, or None. Never raises."""
    token = read_token(path)
    return _jwt_sub(token) if token else None


def _millis(raw) -> datetime | None:
    """A unix-millisecond timestamp, which the payload sends as a digit string."""
    if isinstance(raw, str) and raw.isdigit():
        ms = int(raw)
    elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
        ms = int(raw)
    else:
        return None
    if ms > 10_000_000_000:  # seconds would already be past year 2286
        return datetime.fromtimestamp(ms / 1000, timezone.utc)
    return datetime.fromtimestamp(ms, timezone.utc)


def parse_usage(payload: dict, *, now: float) -> QuotaSnapshot:
    """Build a snapshot from ``planUsage``'s two percents.

    A percent that cannot be read is dropped rather than failing the whole
    snapshot. ``totalPercentUsed`` and the dollar ``limit`` are not windows.
    Both pools reset at ``billingCycleEnd``; the cycle's own length is the
    span, so a 28-day month and a 31-day month each project from their start.
    """
    usage = payload.get("planUsage")
    if not isinstance(usage, dict):
        return QuotaSnapshot(windows=(), fetched_at=now)
    resets = _millis(payload.get("billingCycleEnd"))
    starts = _millis(payload.get("billingCycleStart"))
    span = None
    if resets is not None and starts is not None:
        got = (resets - starts).total_seconds()
        span = got if got > 0 else None
    windows: list[QuotaWindow] = []
    for kind, key in WINDOW_KINDS:
        percent = usage.get(key)
        if not isinstance(percent, (int, float)) or isinstance(percent, bool):
            continue
        windows.append(
            QuotaWindow(
                kind=kind,
                percent=float(percent),
                severity=_severity(float(percent), None),
                resets_at=resets,
                is_active=True,
                span_s=span,
            )
        )
    return QuotaSnapshot(windows=tuple(windows), fetched_at=now)


def fetch_usage(
    token: str, *, timeout: float = 10.0, opener=None, now: float | None = None
) -> QuotaSnapshot:
    """Ask the endpoint. Blocking — callers run it off the event loop.

    ``opener`` is the injection seam for tests; it defaults to
    ``urllib.request.urlopen``.
    """
    request = urllib.request.Request(
        URL,
        data=b"{}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        },
        method="POST",
    )
    open_url = opener or urllib.request.urlopen
    try:
        with open_url(request, timeout=timeout) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            kind = "unauthorized"
        elif exc.code == 429:
            kind = "rate_limited"
        else:
            kind = "unreachable"
        raise QuotaError(kind) from exc
    except Exception as exc:  # noqa: BLE001 — every other failure is the same
        raise QuotaError("unreachable") from exc
    if not isinstance(payload, dict):
        raise QuotaError("unreachable")
    return parse_usage(payload, now=boot_clock() if now is None else now)


PROVIDER = QuotaProvider(
    name="cursor",
    label="Cursor",
    harness="cursor",
    bar_windows=(("auto", "Auto"), ("api", "API")),
    fetch=fetch_usage,
    read_token=read_token,
    account=account_id,
    poll_s=60.0,
    turn_floor_s=30.0,
    window_spans={"auto": MONTH_S, "api": MONTH_S},
)
