"""``aegis gc``: reclaim the disk that old sessions hold (#1005).

The state root grows with every session and nothing else shrinks it. A
session archived more than ``days`` ago keeps its history for later analysis
and loses everything else:

- its transcript is packed: every base64 image in it is emptied (they were 41%
  of the bytes of the largest transcripts, and nothing draws them back), and
  the rest is gzipped to ``<log_id>.jsonl.gz``, which every reader opens
  (transcript/store.py). ``peer_read``, the archive view, the journal and the
  usage reports keep working; ``--resume`` does not, because
- the harness's own copy of the stream is deleted (Claude Code's
  ``projects/*/<resume_id>.jsonl`` and its folder), as are
- its inbox uploads, the files it sent, its stderr log and its browsers'
  folders, and every browser profile no browser has used for ``days``.

A dry run is the default: it lists what would go. Nothing an open session
uses is touched, and a Claude transcript an open session resumes is kept.
"""

from __future__ import annotations

import gzip
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path

from .files import SESSIONS
from .transcript.store import packed

DAYS = 7
# A base64 payload shorter than this is not worth a rewrite.
MIN_IMAGE = 1024


@dataclass
class Report:
    # kind -> [items, bytes]: bytes freed, or that would be freed on a dry run
    # (for transcripts, the size of the plain stores to pack).
    kinds: dict[str, list[int]] = field(default_factory=dict)
    apply: bool = False

    def add(self, kind: str, nbytes: int) -> None:
        row = self.kinds.setdefault(kind, [0, 0])
        row[0] += 1
        row[1] += nbytes

    def lines(self) -> list[str]:
        verb = "freed" if self.apply else "would free"
        out = []
        for kind, (n, b) in self.kinds.items():
            what = "to pack" if kind == "transcripts" and not self.apply else verb
            out.append(f"{kind:<12} {n:>6}  {_gb(b)} {what}")
        if self.apply:
            total = sum(b for _, b in self.kinds.values())
            out.append(f"{'total':<12} {'':>6}  {_gb(total)} freed")
        else:
            out.append("dry run: nothing was deleted; run with --apply to do it")
        return out


def _gb(n: int) -> str:
    return f"{n / 2**30:6.2f} GB"


def _size(path: Path) -> int:
    if path.is_symlink() or path.is_file():
        return path.lstat().st_size
    total = 0
    for root, _, names in os.walk(path):
        for n in names:
            try:
                total += (Path(root) / n).lstat().st_size
            except OSError:
                pass
    return total


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _metas(state_root: Path) -> list[dict]:
    out = []
    for p in sorted((state_root / "sessions").glob("*.json")):
        try:
            m = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(m, dict) and m.get("log_id"):
            out.append(m)
    return out


def _last(meta: dict) -> float:
    return float(meta.get("last_activity") or meta.get("created_at") or time.time())


def strip_images(obj) -> int:
    """Empty every base64 payload in ``obj`` in place; the bytes dropped."""
    n = 0
    if isinstance(obj, dict):
        data = obj.get("data")
        if (
            obj.get("type") == "base64"
            and isinstance(data, str)
            and len(data) >= MIN_IMAGE
        ):
            obj["data"] = ""
            obj["dropped"] = len(data)
            return len(data)
        for v in obj.values():
            n += strip_images(v)
    elif isinstance(obj, list):
        for v in obj:
            n += strip_images(v)
    return n


def _compact(raw: str) -> str:
    """One stored record without its images; a line it cannot parse is kept."""
    if '"base64' not in raw:
        return raw
    try:
        rec = json.loads(raw)
        inner = json.loads(rec["line"])
    except (ValueError, KeyError, TypeError):
        return raw
    if not strip_images(inner):
        return raw
    rec["line"] = json.dumps(inner, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(rec, ensure_ascii=False) + "\n"


def pack(path: Path) -> int:
    """Pack the plain store at ``path`` into ``<path>.gz``; the bytes freed."""
    gz = packed(path)
    part = gz.with_name(gz.name + ".part")
    before = path.stat().st_size
    with (
        path.open(encoding="utf-8", errors="replace", newline="") as src,
        gzip.open(part, "wt", encoding="utf-8", compresslevel=9) as dst,
    ):
        for raw in src:
            dst.write(_compact(raw))
    part.replace(gz)
    path.unlink()
    return before - gz.stat().st_size


def run(
    state_root: Path,
    *,
    days: int = DAYS,
    apply: bool = False,
    claude_home: Path | None = None,
    now: float | None = None,
) -> Report:
    report = Report(apply=apply)
    cutoff = (now or time.time()) - days * 86400
    claude_home = claude_home or Path(
        os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    )
    metas = _metas(state_root)
    # An archived session of another Linux user ran in that user's home (#958).
    old = {
        m["log_id"]: m
        for m in metas
        if m.get("archived") and not m.get("linux_user") and _last(m) < cutoff
    }
    kept_resume = {m.get("resume_id") for m in metas if m["log_id"] not in old} - {None}

    def drop(kind: str, path: Path) -> None:
        if not path.exists() and not path.is_symlink():
            return
        report.add(kind, _size(path))
        if apply:
            _remove(path)

    tdir = state_root / "transcripts"
    projects = claude_home / "projects"
    for log_id, m in old.items():
        store = tdir / f"{log_id}.jsonl"
        if store.exists():
            if apply:
                report.add("transcripts", pack(store))
            else:
                report.add("transcripts", store.stat().st_size)
        drop("inbox", state_root / "inbox" / log_id)
        drop("stderr", state_root / "stderr" / f"{log_id}.log")
        rid = m.get("resume_id") or m.get("claude_session_id")
        if (m.get("harness") or "claude-code") == "claude-code" and rid:
            if rid not in kept_resume and projects.is_dir():
                for p in projects.glob(f"*/{rid}.jsonl"):
                    drop("claude", p)
                    drop("claude", p.with_suffix(""))

    # Sent files: by the session that sent them, or by age for those sent
    # before aegis recorded it.
    fdir = state_root / "files"
    if fdir.is_dir():
        for f in fdir.iterdir():
            if f.name.startswith("."):
                continue
            owner_file = state_root / SESSIONS / f.name
            try:
                owner = owner_file.read_text().strip()
            except OSError:
                owner = ""
            if owner in old or (not owner and f.stat().st_mtime < cutoff):
                drop("files", f)
                if apply:
                    owner_file.unlink(missing_ok=True)

    bdir = state_root / "browsers"
    if bdir.is_dir():
        recent_profiles: set[str] = set()
        for b in sorted(bdir.glob("b-*")):
            try:
                meta = json.loads((b / "meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            created = float(meta.get("created") or b.stat().st_mtime)
            stale = meta.get("archived") and (
                meta.get("session") in old or created < cutoff
            )
            if stale:
                drop("browsers", b)
            elif meta.get("profile"):
                recent_profiles.add(meta["profile"])
        pdir = bdir / "profiles"
        if pdir.is_dir():
            for p in pdir.iterdir():
                if p.name not in recent_profiles and p.stat().st_mtime < cutoff:
                    drop("profiles", p)
    return report
