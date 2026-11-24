"""Read-only repository inventory, and execution of a user's fixed selection.

Only recognized, Git-ignored build products are candidates. The snapshot binds
both source and artifact metadata; execution rechecks it and current users.
No command taken from a repository is executed, including rebuild instructions.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

DAY = 86400
ARTIFACTS = frozenset(
    {
        ".venv",
        "venv",
        "node_modules",
        "target",
        "dist",
        "build",
        ".next",
        ".nuxt",
        ".svelte-kit",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
)
SKIP = ARTIFACTS | {".git", ".aegis", ".claude", ".worktrees", "worktrees"}


def unreadable(error: OSError) -> None:
    raise error


def git(repo: Path, *args: str) -> bytes:
    # Inventory must not refresh the index or run optional Git helpers.
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
        timeout=30,
    ).stdout


def repositories(root: Path) -> list[Path]:
    if (root / ".git").exists():
        return [root]
    base = root / "repos" if (root / "repos").is_dir() else root
    return sorted(
        p
        for p in base.iterdir()
        if not p.is_symlink() and p.is_dir() and (p / ".git").exists()
    )


def tree(path: Path, *, source: bool = False) -> tuple[str, float, int, int]:
    """Metadata digest, newest write, allocated bytes, exclusively held bytes.

    Hard-linked files count in the reclaim estimate only when every link is
    inside this candidate. Shared inodes are never counted twice.
    """
    digest = hashlib.sha256()
    newest = 0.0
    inodes: dict[tuple[int, int], list[int]] = {}
    for base, dirs, files in os.walk(path, followlinks=False, onerror=unreadable):
        if not source and ".git" in files:
            raise ValueError("contains a nested worktree")
        dirs.sort()
        if source:
            dirs[:] = [
                d
                for d in dirs
                if d not in SKIP and not (Path(base) / d / ".git").exists()
            ]
        for name in sorted(files + dirs):
            p = Path(base) / name
            if source and name == ".git":
                continue
            st = p.lstat()
            digest.update(
                repr(
                    (
                        str(p.relative_to(path)),
                        st.st_dev,
                        st.st_ino,
                        st.st_size,
                        st.st_mtime_ns,
                        st.st_ctime_ns,
                        st.st_mode,
                    )
                ).encode()
            )
            if not p.is_dir():
                newest = max(newest, st.st_mtime)
            if not source and stat.S_ISREG(st.st_mode):
                key = (st.st_dev, st.st_ino)
                item = inodes.setdefault(key, [0, st.st_nlink, st.st_blocks * 512])
                item[0] += 1
        if not source:
            # Never cross symlinks, mount points, or nested repositories.
            if any((Path(base) / d).is_mount() or d == ".git" for d in dirs):
                raise ValueError("contains a mount point or nested repository")
    return (
        digest.hexdigest(),
        newest,
        sum(v[2] for v in inodes.values()),
        sum(v[2] for v in inodes.values() if v[0] == v[1]),
    )


def rebuild(path: Path, repo: Path) -> str | None:
    """Evidence stronger than a familiar directory name or an ignore rule."""
    parent = path.parent
    if path.name == "__pycache__":
        if not all(p.is_file() and p.suffix == ".pyc" for p in path.iterdir()):
            return None
    elif path.name in {".pytest_cache", ".mypy_cache", ".ruff_cache"}:
        if not (path / "CACHEDIR.TAG").is_file():
            return None
    if path.name in {".venv", "venv"}:
        if not (path / "pyvenv.cfg").is_file():
            return None
        allowed = {
            "bin",
            "lib",
            "lib64",
            "include",
            "share",
            "Scripts",
            "Lib",
            "Include",
            "pyvenv.cfg",
            ".gitignore",
            "CACHEDIR.TAG",
            ".Python",
        }
        if any(p.name not in allowed for p in path.iterdir()):
            return None
    for base in (parent, *parent.parents):
        if not base.is_relative_to(repo):
            break
        if path.name in {".venv", "venv"} and (base / "pyproject.toml").is_file():
            return f"Recreate the Python environment from {base / 'pyproject.toml'} (for example, uv sync)."
        if (
            path.name == "target"
            and (base / "Cargo.toml").is_file()
            and (path / ".rustc_info.json").is_file()
        ):
            return f"Run cargo build in {base}."
        if (base / "package.json").is_file():
            pkg = json.loads((base / "package.json").read_text())
            if path.name == "node_modules":
                for lock, command in (
                    ("package-lock.json", "npm ci"),
                    ("pnpm-lock.yaml", "pnpm install --frozen-lockfile"),
                    ("yarn.lock", "yarn install --immutable"),
                    ("bun.lock", "bun install --frozen-lockfile"),
                ):
                    if (base / lock).is_file():
                        return f"Run {command} in {base}."
            if path.name in {
                "dist",
                "build",
                ".next",
                ".nuxt",
                ".svelte-kit",
            } and pkg.get("scripts", {}).get("build"):
                return f"Run the package.json build script in {base}."
        if path.name in {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}:
            return "Regenerated by Python, pytest, mypy or ruff when next run."
    return None


def users() -> tuple[set[Path], list[bytes]]:
    """Process files and installed launchers, including scripts and symlinks."""
    paths: set[Path] = set()
    scripts = []
    proc = Path("/proc")
    if not proc.is_dir():
        raise OSError("process reference inspection requires /proc")
    for pid in proc.iterdir():
        if not pid.name.isdigit():
            continue
        try:
            refs = [pid / "cwd", pid / "exe", *(pid / "fd").iterdir()]
            for ref in refs:
                try:
                    value = os.readlink(ref)
                    if value.startswith("/"):
                        paths.add(Path(value.removesuffix(" (deleted)")))
                except OSError:
                    pass
            for value in (pid / "cmdline").read_bytes().split(b"\0"):
                if value.startswith(b"/"):
                    paths.add(Path(os.fsdecode(value)))
            for line in (pid / "maps").read_text().splitlines():
                fields = line.split(maxsplit=5)
                if len(fields) == 6 and fields[5].startswith("/"):
                    paths.add(Path(fields[5].removesuffix(" (deleted)")))
        except (OSError, ProcessLookupError):
            continue
    bins = {Path(p) for p in os.get_exec_path()} | {Path.home() / ".local/bin"}
    for base in bins:
        if not base.is_dir():
            continue
        for p in base.iterdir():
            try:
                if p.is_symlink():
                    paths.add(p.resolve())
                elif p.is_file():
                    with p.open("rb") as stream:
                        scripts.append(stream.read(16384))
            except OSError:
                continue
    return paths, scripts


def scan(
    root: Path,
    *,
    days: int = 30,
    active: tuple[Path, ...] = (),
    now: float | None = None,
) -> dict:
    root = root.resolve(strict=True)
    now = time.time() if now is None else now
    cutoff = now - days * DAY
    refs, launchers = users()
    rows = []
    for repo in repositories(root):
        try:
            source_stamp, source_at, _, _ = tree(repo, source=True)
            tracked = set(
                os.fsdecode(p) for p in git(repo, "ls-files", "-z").split(b"\0") if p
            )
            dirty = git(repo, "status", "--porcelain=v1", "--untracked-files=no")
            for base, dirs, _ in os.walk(repo, followlinks=False, onerror=unreadable):
                dirs.sort()
                for name in dirs[:]:
                    p = Path(base) / name
                    if name in SKIP or p.is_symlink() or (p / ".git").exists():
                        dirs.remove(name)
                    if name not in ARTIFACTS or p.is_symlink():
                        continue
                    relative = str(p.relative_to(repo))
                    row: dict = {
                        "path": str(p),
                        "repo": str(repo),
                        "kind": name,
                        "source_at": source_at,
                        "source_stamp": source_stamp,
                        "bytes": 0,
                        "reclaimable_bytes": 0,
                        "artifact_at": None,
                        "rebuild": None,
                        "reason": None,
                    }
                    try:
                        if p.is_mount():
                            raise ValueError("artifact is a mount point")
                        fingerprint, at, size, reclaim = tree(p)
                        st = p.lstat()
                        row.update(
                            stamp=fingerprint,
                            identity=[st.st_dev, st.st_ino],
                            artifact_at=at,
                            bytes=size,
                            reclaimable_bytes=reclaim,
                        )
                        row["rebuild"] = rebuild(p, repo)
                        if any(
                            t == relative or t.startswith(relative + "/")
                            for t in tracked
                        ):
                            row["reason"] = "contains Git-tracked files"
                        elif (
                            subprocess.run(
                                [
                                    "git",
                                    "-C",
                                    str(repo),
                                    "check-ignore",
                                    "-q",
                                    "--",
                                    relative,
                                ],
                                capture_output=True,
                                timeout=30,
                            ).returncode
                            != 0
                        ):
                            row["reason"] = "directory is not Git-ignored"
                        elif not row["rebuild"]:
                            row["reason"] = (
                                "rebuild evidence is missing or contents are unclassified"
                            )
                        elif dirty:
                            row["reason"] = "repository has tracked changes"
                        elif source_at > cutoff:
                            row["reason"] = "recent source activity"
                        elif at > cutoff:
                            row["reason"] = "recent artifact activity"
                        elif any(a == repo or a.is_relative_to(repo) for a in active):
                            row["reason"] = "active session or worktree"
                        elif any(
                            ref == repo or ref.is_relative_to(repo) for ref in refs
                        ):
                            row["reason"] = (
                                "repository is referenced by a process or installed command"
                            )
                        elif any(str(p).encode() in body for body in launchers):
                            row["reason"] = (
                                "environment is referenced by an installed launcher"
                            )
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        row["reason"] = f"inspection failed: {exc}"
                    rows.append(row)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            rows.append(
                {
                    "path": str(repo),
                    "repo": str(repo),
                    "reason": f"repository inspection failed: {exc}",
                    "bytes": 0,
                    "reclaimable_bytes": 0,
                }
            )
    return {
        "root": str(root),
        "days": days,
        "created": now,
        "rows": rows,
        "free_bytes": shutil.disk_usage(root).free,
    }


def execute(
    proposal: dict, selected: list[str], *, active: tuple[Path, ...] = ()
) -> dict:
    """Only a user-facing caller may invoke this after approval. No discovery expansion."""
    approved = {r["path"]: r for r in proposal["rows"] if not r.get("reason")}
    if (
        not selected
        or len(set(selected)) != len(selected)
        or set(selected) - approved.keys()
    ):
        raise ValueError(
            "select at least one eligible path from this proposal, without duplicates"
        )
    root = Path(proposal["root"])
    before = shutil.disk_usage(root).free
    removed, skipped, errors = [], [], []
    # Rescan at execution, including live process and session references.
    current = {
        r["path"]: r for r in scan(root, days=proposal["days"], active=active)["rows"]
    }
    for name in selected:
        original, row = approved[name], current.get(name)
        if (
            not row
            or row.get("reason")
            or any(
                row.get(k) != original.get(k)
                for k in ("stamp", "source_stamp", "identity")
            )
        ):
            skipped.append(
                {
                    "path": name,
                    "reason": (row or {}).get("reason") or "changed since the proposal",
                }
            )
            continue
        path, repo = Path(name), Path(row["repo"])
        try:
            # A full inventory may take time. Check writes and references
            # again at the selected path, immediately before deletion.
            refs, launchers = users()
            if (
                tree(path)[0] != original["stamp"]
                or tree(repo, source=True)[0] != original["source_stamp"]
                or any(ref == repo or ref.is_relative_to(repo) for ref in refs)
                or any(name.encode() in body for body in launchers)
            ):
                skipped.append(
                    {
                        "path": name,
                        "reason": "changed or started being used during execution",
                    }
                )
                continue
            before_status = git(
                repo, "status", "--porcelain=v1", "--untracked-files=no"
            )
            # Open every parent without following symlinks, then let rmtree's
            # fd-based implementation refuse a root replaced by a symlink.
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                for component in path.parent.relative_to(root).parts:
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=fd,
                    )
                    os.close(fd)
                    fd = next_fd
                st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                if [st.st_dev, st.st_ino] != row["identity"]:
                    raise ValueError("directory was replaced")
                shutil.rmtree(path.name, dir_fd=fd)
            finally:
                os.close(fd)
            removed.append(name)
            if (
                git(repo, "status", "--porcelain=v1", "--untracked-files=no")
                != before_status
            ):
                errors.append(
                    {
                        "path": str(repo),
                        "reason": "tracked-file state changed during cleanup",
                    }
                )
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            errors.append({"path": name, "reason": str(exc)})
    after = shutil.disk_usage(root).free
    return {
        "removed": removed,
        "skipped": skipped,
        "errors": errors,
        "free_before": before,
        "free_after": after,
        "recovered_bytes": after - before,
        "tracked_state_preserved": not errors,
    }
