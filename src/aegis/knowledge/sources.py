"""File sources scanned in a worker thread and published one root at a time."""

from __future__ import annotations

import asyncio
import ctypes
import errno
import fcntl
import hashlib
import os
import secrets
import signal
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ..ops import OpError
from .importer import MAX_FILE_BYTES, SKIP_DIRS, regular_bytes
from .items import Item, from_bytes, parse_skill
from .know_how import DOC_NAME, parse_doc

Format = Literal["markdown", "skill", "know-how"]
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
WRITE_TEMP = ".aegis-write-"
RENAME_EXCHANGE = 2
_renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)


@dataclass(frozen=True)
class Source:
    root: Path
    format: Format
    include: tuple[str, ...] = ("**/*.md",)
    exclude: tuple[str, ...] = ()
    # Know-how IDs remain relative to the Git worktree, as in the direct reader.
    worktree: Path | None = None
    private: Path | None = None
    optional: bool = False
    writable: bool = False


@dataclass(frozen=True)
class Entry:
    item: Item
    text: str
    files: tuple[str, ...]
    signature: tuple[int, ...]


@dataclass
class Snapshot:
    entries: dict[str, Entry]
    errors: dict[str, str]


def scan(source: Source, previous: Snapshot | None) -> Snapshot:
    entries: dict[str, Entry] = {}
    errors: dict[str, str] = {}
    files: list[PurePosixPath] = []
    old = previous.entries if previous else {}

    def walk(fd: int, directory: PurePosixPath) -> None:
        try:
            with os.scandir(fd) as children:
                for child in children:
                    visit(fd, directory / child.name)
        except OSError as exc:
            errors[str(directory)] = exc.strerror or str(exc)

    def visit(fd: int, relative: PurePosixPath) -> None:
        name = relative.name
        if (
            name.startswith(WRITE_TEMP)
            or name in SKIP_DIRS
            or any(relative.full_match(p) for p in source.exclude)
        ):
            return
        key = str(relative)
        path = source.root / key
        if source.private is not None and path.is_relative_to(source.private):
            return
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(st.st_mode):
                errors[key] = "symbolic links are skipped"
                return
            if stat.S_ISDIR(st.st_mode):
                if source.format != "know-how":
                    sub = os.open(name, DIR_FLAGS, dir_fd=fd)
                    try:
                        walk(sub, relative)
                    finally:
                        os.close(sub)
                return
            if not stat.S_ISREG(st.st_mode):
                return
            if source.format == "skill" and name != "SKILL.md":
                files.append(relative)
                return
            if source.format == "know-how" and not DOC_NAME.fullmatch(name):
                return
            if not any(relative.full_match(p) for p in source.include):
                return
            signature = (
                st.st_dev,
                st.st_ino,
                st.st_mtime_ns,
                st.st_ctime_ns,
                st.st_size,
            )
            if (cached := old.get(key)) is not None and cached.signature == signature:
                entries[key] = cached
                return
            root = source.worktree or source.root
            item, text = from_bytes(
                root,
                path,
                parse_skill if source.format == "skill" else parse_doc,
                relative.parent.name if source.format == "skill" else relative.stem,
                scope="workspace",
                data=regular_bytes(Path(name), MAX_FILE_BYTES, dir_fd=fd),
            )
            entries[key] = Entry(item, text, (), signature)
        except (OSError, ValueError, OpError) as exc:
            errors[key] = str(exc)

    # Open each component so a replaced ancestor cannot redirect a scan.
    try:
        with open_directory(source.root) as fd:
            walk(fd, PurePosixPath())
    except FileNotFoundError as exc:
        if source.format != "know-how" and not source.optional:
            errors["."] = exc.strerror or str(exc)
    except OSError as exc:
        errors["."] = exc.strerror or str(exc)

    if source.format == "skill":
        bundles: dict[PurePosixPath, list[str]] = {
            PurePosixPath(key).parent: [] for key in entries
        }
        for path in files:
            for parent in path.parents:
                if parent in bundles:
                    bundles[parent].append(str(path.relative_to(parent)))
                    break
        entries = {
            key: Entry(
                entry.item,
                entry.text,
                tuple(sorted(bundles[PurePosixPath(key).parent])),
                entry.signature,
            )
            for key, entry in entries.items()
        }
    return Snapshot(entries, errors)


@contextmanager
def open_directory(path: Path):
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in path.parts[1:]:
            child = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


def stale(revision: str | None) -> OpError:
    return OpError(
        "stale",
        f"entry changed; current revision is {revision or 'absent'}. Read it again before saving.",
        {"revision": revision},
    )


def _exchange(fd: int, first: str, second: str) -> None:
    """Swap two names atomically, keeping the exact file that was displaced."""
    if _renameat2 is None:
        raise OSError(errno.ENOSYS, "knowledge writes require atomic file exchange")
    if _renameat2(fd, os.fsencode(first), fd, os.fsencode(second), RENAME_EXCHANGE):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _verify_displaced(fd: int, name: str, expected: str) -> None:
    """Only retire a displaced file when no outside editor has it open."""
    flags = os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
    with os.fdopen(os.open(name, flags, dir_fd=fd), "rb") as previous:
        file_fd = previous.fileno()
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OpError(
                "write_conflict", "the displaced page is not an unshared regular file"
            )
        # A scanner racing to open this private name must never deliver the
        # default lease-break signal (SIGIO) to the server.
        fcntl.fcntl(file_fd, fcntl.F_SETSIG, signal.SIGURG)
        try:
            fcntl.fcntl(file_fd, fcntl.F_SETLEASE, fcntl.F_WRLCK)
        except OSError as exc:
            raise OpError(
                "write_conflict", "the displaced page is open or cannot be leased"
            ) from exc
        data = previous.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise OpError("write_conflict", "the displaced page exceeds the size limit")
        revision = hashlib.sha256(data).hexdigest()
        if revision != expected:
            raise stale(revision)
        os.unlink(name, dir_fd=fd)


def write_page(source: Source, entry: Entry, text: str, expected_revision: str) -> Item:
    """Replace one cataloged page. Directory locking serializes Aegis writers."""
    path = entry.item.path
    assert path is not None and path.is_relative_to(source.root)
    data = text.encode()
    if len(data) > MAX_FILE_BYTES:
        raise OpError(
            "bad_params", f"a page may contain at most {MAX_FILE_BYTES} bytes"
        )
    try:
        item, _ = from_bytes(
            source.worktree or source.root,
            path,
            parse_skill if source.format == "skill" else parse_doc,
            entry.item.name,
            scope="workspace",
            data=data,
        )
    except ValueError as exc:
        raise OpError("bad_params", str(exc)) from None

    try:
        with open_directory(path.parent) as fd:
            fcntl.flock(fd, fcntl.LOCK_EX)
            mode = os.stat(path.name, dir_fd=fd, follow_symlinks=False).st_mode
            if (
                not stat.S_ISREG(mode)
                or not mode & 0o222
                or not os.access(
                    path.name,
                    os.W_OK,
                    dir_fd=fd,
                    effective_ids=True,
                    follow_symlinks=False,
                )
            ):
                raise OpError("forbidden", "the knowledge page is read-only")
            temp = WRITE_TEMP + secrets.token_hex(12)
            exchanged = False
            out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=fd)
            try:
                with os.fdopen(out, "wb") as file:
                    file.write(data)
                    os.fchmod(file.fileno(), mode & 0o777)
                current = regular_bytes(Path(path.name), MAX_FILE_BYTES, dir_fd=fd)
                revision = hashlib.sha256(current).hexdigest()
                if revision != expected_revision:
                    raise stale(revision)
                if current != data:
                    _exchange(fd, temp, path.name)
                    exchanged = True
                    try:
                        _verify_displaced(fd, temp, expected_revision)
                    except (OSError, OpError) as exc:
                        recovery = str(path.with_name(temp))
                        try:
                            _exchange(fd, temp, path.name)
                        except OSError as restore:
                            raise OpError(
                                "write_conflict",
                                f"could not restore the displaced page; preserved at {recovery}",
                                {"recovery_path": recovery},
                            ) from restore
                        code = (
                            exc.code if isinstance(exc, OpError) else "write_conflict"
                        )
                        message = exc.message if isinstance(exc, OpError) else str(exc)
                        details = (exc.data or {}) if isinstance(exc, OpError) else {}
                        raise OpError(
                            code,
                            f"{message} Restored the displaced page; other contents are at {recovery}.",
                            {**details, "recovery_path": recovery},
                        ) from exc
            finally:
                if not exchanged:
                    try:
                        os.unlink(temp, dir_fd=fd)
                    except FileNotFoundError:
                        pass
    except FileNotFoundError:
        raise stale(None) from None
    except PermissionError:
        raise OpError("forbidden", "the knowledge page cannot be written") from None
    except OSError as exc:
        raise OpError("write_failed", exc.strerror or str(exc)) from None
    return item


class Sources:
    def __init__(self) -> None:
        self._held: dict[Source, tuple[float, Snapshot]] = {}
        self._running: dict[Source, asyncio.Task[Snapshot]] = {}
        self._configured: set[Source] = set()

    def configure(self, sources: list[Source]) -> None:
        current = set(sources)
        for removed in self._configured - current:
            self._held.pop(removed, None)
            self._running.pop(removed, None)
        self._configured = current

    def invalidate(self, path: Path) -> None:
        for source in self._held.keys() | self._running.keys():
            if path.is_relative_to(source.root):
                self._held.pop(source, None)
                self._running.pop(source, None)

    def snapshot(self, source: Source) -> Snapshot | None:
        held = self._held.get(source)
        return held[1] if held is not None else None

    def worktrees(self) -> set[Source]:
        return {source for source in self._held if source.worktree is not None}

    async def refresh(self, source: Source, now: float, every: float) -> Snapshot:
        held = self._held.get(source)
        if held is not None and now - held[0] < every:
            return held[1]

        async def rebuild() -> Snapshot:
            try:
                result = await asyncio.to_thread(
                    scan, source, held[1] if held else None
                )
                if self._running.get(source) is not asyncio.current_task():
                    return result  # configuration retired this generation
                self._held.pop(source, None)
                self._held[source] = (now, result)
                # Registered roots stay cached. Bound the recently read worktrees.
                worktrees = [root for root in self._held if root.worktree is not None]
                if len(worktrees) > 32:
                    del self._held[worktrees[0]]
                return result
            finally:
                if self._running.get(source) is asyncio.current_task():
                    self._running.pop(source, None)

        # One scan per source. An older overlapping generation cannot publish
        # after a newer one. Cancelling a reader does not abandon its scan.
        task = self._running.get(source)
        if task is None:
            task = self._running[source] = asyncio.create_task(rebuild())
        return await asyncio.shield(task)
