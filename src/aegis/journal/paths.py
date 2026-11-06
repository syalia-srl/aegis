"""Name files the way the journal stores them.

A touch is named by its repo's main checkout and its path inside the repo, so an
edit made in a worktree and one made in the main checkout are the same file to
the journal. A path in no repo keeps its absolute real path. Claims do not use
this module: they match real paths, which is what exempts worktrees from them.

Everything here runs git, so it runs on the journal's writer thread. git runs
as ``user``, the Linux user of the session whose turn it reads (launch.py):
git refuses a repository another user owns.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Iterable

from .. import launch


def _git(args: list[str], cwd: str) -> str | None:
    try:
        r = launch.run(["git", *args], cwd=cwd, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_binary(args: list[str], cwd: str) -> bytes | None:
    """Run git and return binary output (for -z handling)."""
    try:
        r = launch.run(["git", *args], cwd=cwd, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def _existing(path: str) -> str:
    """The path, or its nearest ancestor that exists."""
    d = path
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return d


_checkout_cache: dict[str, tuple[str, str]] = {}


def _checkout(directory: str) -> tuple[str, str] | None:
    """(this worktree's top level, the main checkout) for a directory in a repo."""
    if directory in _checkout_cache:
        return _checkout_cache[directory]
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], directory)
    if not out:
        return None
    top, common = out.splitlines()[:2]
    common = os.path.realpath(os.path.join(directory, common))
    main = os.path.dirname(common) if os.path.basename(common) == ".git" else common
    result = os.path.realpath(top), main
    _checkout_cache[directory] = result
    return result


def place(path: str) -> tuple[str | None, str, str | None]:
    """(the main checkout, the path inside the worktree, the worktree's top
    level) for a path in a repo; (None, the real path, None) outside one."""
    try:
        path = os.path.realpath(path)
    except ValueError:
        # NUL byte or other invalid input
        return None, path, None
    hit = _checkout(_existing(path))
    if hit is None:
        return None, path, None
    top, main = hit
    rel = os.path.relpath(path, top)
    if rel.startswith(".."):
        return None, path, None
    # A file whose directory is gone was in a tree since removed (a worktree):
    # the nearest existing ancestor's checkout is not where it was written.
    if not os.path.isdir(os.path.dirname(path)):
        return main, "" if rel == "." else rel, None
    return main, "" if rel == "." else rel, top


def name(path: str) -> tuple[str | None, str]:
    main, rel, _ = place(path)
    return main, rel


def full(repo: str | None, rel: str) -> str:
    if repo is None:
        return rel
    return os.path.join(repo, rel) if rel else repo


def _where(directory: str) -> str:
    """Where git runs for ``directory``: its nearest existing ancestor."""
    return _existing(os.path.realpath(directory))


def full_sha(directory: str, commit: str) -> str | None:
    out = _git(
        ["rev-parse", "--verify", "--end-of-options", f"{commit}^{{commit}}"],
        _where(directory),
    )
    return out.strip() if out else None


def commit_files(directory: str, commit: str) -> list[str] | None:
    out = _git_binary(
        [
            "show",
            "-z",
            "--name-only",
            "--format=",
            "--end-of-options",
            f"{commit}^{{commit}}",
        ],
        _where(directory),
    )
    if out is None:
        return None
    # Split on null bytes and decode UTF-8, dropping empty strings
    return [name.decode("utf-8") for name in out.split(b"\0") if name]


def commit_by_subject(directory: str, subject: str, ts: float) -> str | None:
    """The full hash of the newest commit on any ref with exactly this subject,
    made by ``ts`` plus a minute for the clock; for a commit that printed no hash.
    None for one no ref holds any more, such as a deleted branch's."""
    out = _git(
        [
            "log",
            "--all",
            "--format=%H%x00%s",
            "--fixed-strings",
            f"--grep={subject}",
            f"--until={int(ts) + 60}",
        ],
        _where(directory),
    )
    for line in (out or "").splitlines():
        h, _, s = line.partition("\0")
        if s == subject:
            return h
    return None


# The owner/name at the end of a remote URL, SSH or HTTPS, without .git.
SLUG = re.compile(r"[:/]([^/:\s]+)/([^/:\s]+?)(?:\.git)?/?$")
_slug_cache: dict[str, str | None] = {}


def remote_slug(directory: str) -> str | None:
    """The owner/name of a checkout's origin, or None when it has none."""
    if directory in _slug_cache:
        return _slug_cache[directory]
    out = _git(["remote", "get-url", "origin"], directory)
    m = SLUG.search(out.strip()) if out else None
    slug = f"{m[1]}/{m[2]}" if m else None
    _slug_cache[directory] = slug
    return slug


def checkout_of(slug: str, tops: Iterable[str]) -> str | None:
    """The first of the tops whose origin is the owner/name slug, any case."""
    want = slug.lower()
    for top in tops:
        got = remote_slug(top)
        if got and got.lower() == want:
            return top
    return None
