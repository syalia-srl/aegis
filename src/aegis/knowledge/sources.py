"""Read-only sources scanned in a worker thread and published one root at a time."""

from __future__ import annotations

import asyncio
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ..ops import OpError
from .importer import MAX_FILE_BYTES, SKIP_DIRS, regular_bytes
from .items import Item, from_bytes, parse_skill
from .know_how import DOC_NAME, parse_doc

Format = Literal["markdown", "skill", "know-how"]
DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


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
        if name in SKIP_DIRS or any(relative.full_match(p) for p in source.exclude):
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
    fd = os.open("/", DIR_FLAGS)
    try:
        for part in source.root.parts[1:]:
            sub = os.open(part, DIR_FLAGS, dir_fd=fd)
            os.close(fd)
            fd = sub
        walk(fd, PurePosixPath())
    except FileNotFoundError as exc:
        if source.format != "know-how" and not source.optional:
            errors["."] = exc.strerror or str(exc)
    except OSError as exc:
        errors["."] = exc.strerror or str(exc)
    finally:
        os.close(fd)

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
