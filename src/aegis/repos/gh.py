"""A branch's pull requests from ``gh``, cached, and nothing when gh is missing,
not logged in, or the repo is not on GitHub."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time

from .. import launch

TTL = 120.0
TIMEOUT = 8.0

_cache: dict[tuple[str, str], tuple[float, list[dict] | None]] = {}
# Builds run in threads and forget() on the loop.
_lock = threading.Lock()


def _ci(rollup) -> str:
    states = [
        (c.get("conclusion") or c.get("state") or c.get("status") or "").upper()
        for c in rollup or []
    ]
    if not states:
        return ""
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") for s in states):
        return "CI failing"
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states):
        return "CI passing"
    return "CI running"


FIELDS = "number,title,state,url,statusCheckRollup"


def _row(p: dict) -> dict:
    return {
        "n": p["number"],
        "url": p["url"],
        "title": p.get("title", ""),
        "state": p.get("state", "").lower(),
        "ci": _ci(p.get("statusCheckRollup")),
    }


def _ask(top: str, key: str, args: list[str], parse):
    """gh's answer to ``args`` run in ``top``, parsed and cached under
    ``(top, key)``; ``None`` when gh could not answer."""
    with _lock:
        hit = _cache.get((top, key))
    if hit and time.monotonic() - hit[0] < TTL:
        return hit[1]
    found = None
    if shutil.which("gh"):
        try:
            r = launch.run(
                ["gh", *args],
                cwd=top,
                text=True,
                errors="replace",
                timeout=TIMEOUT,
            )
            if r.returncode == 0:
                found = parse(r.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            found = None
    with _lock:
        _cache[(top, key)] = (time.monotonic(), found)
    return found


def prs(top: str, branch: str) -> list[dict] | None:
    """``[{n, url, title, state, ci}]`` for PRs whose head is ``branch``, newest
    first; ``None`` when gh could not answer."""
    if not branch:
        return None
    return _ask(
        top,
        branch,
        ["pr", "list", "--head", branch, "--state", "all", "--limit", "20"]
        + ["--json", FIELDS],
        lambda out: [_row(p) for p in json.loads(out or "[]")],
    )


def pr(top: str, n: int) -> dict | None:
    """``{n, url, title, state, ci}`` of PR ``n`` in the repo of ``top``, read
    now: a PR seen opened may have merged anywhere since; ``None`` when gh
    could not answer."""
    return _ask(
        top,
        f"#{n}",
        ["pr", "view", str(n), "--json", FIELDS],
        lambda out: _row(json.loads(out)),
    )


def forget(top: str) -> None:
    """Drop what is cached for a worktree: a PR was just opened or merged there."""
    with _lock:
        for k in [k for k in _cache if k[0] == top]:
            del _cache[k]
