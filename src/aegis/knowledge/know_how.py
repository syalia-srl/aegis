"""A repository's know-how: its ``know-how/*.md`` menu, once per session.

The docs stay in the repository's git tree: they are reviewed there, reach
every contributor and differ by branch, none of which holds for the per-person
store. aegis keeps no copy and ranks nothing. Ranking on a message put the doc
an agent went on to read in the top 3 no more often than 3 random docs of that
repository, since messages are short and the need shows up later in the turn;
so the agent gets the whole menu and chooses.

A session is told about a repository when it first touches it: its working
directory at the first send, and any path a tool call of its own names after
that. The menu is owed like a notice (``pending``), so it rides on a send or a
tool call the same way. ``Session.know_how_sent`` names the main checkouts it
was told about, so a worktree counts with its main checkout, a restart repeats
nothing and a compaction, which can drop the menu, tells it again.

A cloned repository's text reaches the agent with no approval, so each
description is one flattened, cut line behind the doc's path and can never look
like a message header. Files are read off the event loop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .importer import MAX_FILE_BYTES, regular_bytes
from .items import FRONT, Item, _split, read_item, source_id
from ..journal import paths, shell
from ..ops import OpError
from ..transcript.entries import NOTICE_HEADER

if TYPE_CHECKING:
    from ..session import Session

log = logging.getLogger("aegis.know_how")

FOLDER = "know-how"
MAX_DOCS = 50
DESCRIPTION_CHARS = 200
HEAD_BYTES = 4096
FIRST_SEND_WAIT_S = 2.0
MAX_WORDS = 20
DOC_NAME = re.compile(r"^[\w.-]+\.md$")
FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)^---[ \t]*$", re.S | re.M)
WHEN = re.compile(r"^when:[ \t]*(.*)$", re.M)
BOLD_WHEN = re.compile(r"\*\*When to reach for this:\*\*[ \t]*(.*)")
HEADING = re.compile(r"^#+[ \t]+(.*)$", re.M)
PATH_KEYS = ("file_path", "notebook_path", "path", "cwd", "workdir")
PATH_WORD = re.compile(r"[^\s'\"`=:;|&()<>]*/[^\s'\"`=:;|&()<>]*")


def describe(text: str) -> str:
    """When to read a doc: its frontmatter's ``when:``, else a ``**When to
    reach for this:**`` line, else its first heading. One line, cut."""
    front = FRONTMATTER.match(text)
    when = WHEN.search(front.group(1)) if front else None
    found = when or BOLD_WHEN.search(text) or HEADING.search(text)
    line = " ".join(found.group(1).split()) if found else ""
    return line[:DESCRIPTION_CHARS]


def parse_doc(text: str, name: str, path: Path) -> Item:
    text = text.replace("\r\n", "\n")
    meta, body = _split(text) if FRONT.match(text) else ({}, text)
    description = str(meta.get("when") or meta.get("description") or describe(text))
    return Item("skill", name, description, body, meta, path)


def read_entry(
    cwd: str, *, entry_id: str | None, relative_path: str | None
) -> tuple[Item, str] | None:
    """Read a know-how document in the given session's own Git worktree."""
    if not cwd or not Path(cwd).is_dir():
        return None
    _, _, top = paths.place(cwd)
    if top is None:
        return None
    root = Path(top)
    if entry_id is not None:
        source, _, relative_path = entry_id.partition(":")
        if source != source_id(root):
            return None
    parts = (relative_path or "").split("/")
    if len(parts) != 2 or parts[0] != FOLDER or not DOC_NAME.fullmatch(parts[1]):
        return None
    name = parts[1]
    path = root / FOLDER / name
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        root_fd = os.open(root, flags)
        try:
            folder_fd = os.open(FOLDER, flags, dir_fd=root_fd)
            try:
                return read_item(
                    root,
                    path,
                    parse_doc,
                    path.stem,
                    scope="workspace",
                    read_bytes=lambda _: regular_bytes(
                        Path(name), MAX_FILE_BYTES, dir_fd=folder_fd
                    ),
                )
            finally:
                os.close(folder_fd)
        finally:
            os.close(root_fd)
    except (OSError, ValueError, OpError):
        return None


def read_docs(top: str) -> tuple[list[tuple[str, str]], int]:
    """(name, description) of the first docs in ``top``'s know-how folder, and
    how many it holds in all. A folder that cannot be read holds none."""
    # Shared scanner supplies the same pages as the catalog (including its
    # no-symlink rules). The menu limit applies after matching the entries.
    from .sources import Source, scan

    root = Path(top)
    if not (root / FOLDER).is_dir():
        return [], 0
    snapshot = scan(Source(root / FOLDER, "know-how", worktree=root), None)
    names = sorted(snapshot.entries)
    return [
        (name, describe(snapshot.entries[name].text[:HEAD_BYTES]))
        for name in names[:MAX_DOCS]
    ], len(names)


def block(main: str, top: str, cwd: str, docs: list[tuple[str, str]], n: int) -> str:
    """The menu an agent reads: paths it can open from where it runs."""
    # A list, so the transcript's Markdown keeps one doc to a line.
    head = (
        f"{NOTICE_HEADER}{FOLDER} · {os.path.basename(main)}\n"
        "This repository's procedure docs. Read those that match your task "
        "before you act:\n\n"
    )
    lines = []
    root_id = source_id(Path(top))
    for name, description in docs:
        path = os.path.join(top, FOLDER, name)
        near = os.path.relpath(path, cwd)
        path = path if near.startswith("..") else near
        line = f"- {path}: {description}" if description else f"- {path}"
        lines.append(f"{line} [{root_id}:{FOLDER}/{name}]")
    if n > len(docs):
        lines.append(f"- … and {n - len(docs)} more in {FOLDER}/")
    return head + "\n".join(lines)


def candidates(tool_input: dict, cwd: str) -> list[str]:
    """The paths a tool call names: its path arguments, and for a shell command
    the directory it runs in and its words holding a ``/``."""
    out = [
        os.path.join(cwd, v)
        for k in PATH_KEYS
        if isinstance(v := tool_input.get(k), str) and v
    ]
    command = tool_input.get("command") or tool_input.get("cmd")
    if isinstance(command, str):
        base = shell.leading_cd(command, cwd) or cwd
        out.append(base)
        out += [os.path.join(base, w) for w in PATH_WORD.findall(command)[:MAX_WORDS]]
    return out


@dataclass
class _Seen:
    """One session's walk: dropped at a compaction and when it closes."""

    cwd: str
    started: bool = False
    dirs: set[str] = field(default_factory=set)
    empty: set[str] = field(default_factory=set)  # main checkouts with no docs
    owed: dict[str, tuple[Path, str]] = field(default_factory=dict)


class KnowHow:
    def __init__(
        self, can_read: Callable[["Session", Path], bool] | None = None
    ) -> None:
        self._can_read = can_read or (lambda _session, _root: True)
        self._seen: dict[str, _Seen] = {}

    def _state(self, s: "Session") -> _Seen:
        if s.log_id not in self._seen:
            self._seen[s.log_id] = _Seen(str(s.spec.cwd))
        return self._seen[s.log_id]

    def touched(self, s: "Session", tool_input: dict) -> None:
        """A tool call of the session's own: look at the directories it names
        for the first time, off the loop. On the loop, so it never blocks."""
        st = self._state(s)
        command = tool_input.get("command")
        new = []
        for p in candidates(tool_input, st.cwd):
            d = os.path.normpath(p)
            if d not in st.dirs:
                st.dirs.add(d)
                new.append(d)
        # Only Claude Code's Bash keeps its directory between calls.
        if isinstance(command, str) and s.harness.src == "claude":
            st.cwd = shell.leading_cd(command, st.cwd) or st.cwd
        if new:
            asyncio.get_running_loop().create_task(self._look_and_steer(s, new))

    async def _look_and_steer(self, s: "Session", found: list[str]) -> None:
        try:
            await self._look(s, found)
        except Exception:
            log.exception("%s: could not read know-how", s.handle)
            return
        if self._state(s).owed:
            s._steer_owed()

    async def ready(self, s: "Session") -> None:
        """Before the session's first send: its working directory's menu, so a
        session started in a repository has it on its first message."""
        st = self._state(s)
        if st.started:
            return
        st.started = True
        d = os.path.normpath(st.cwd)
        st.dirs.add(d)
        try:
            await asyncio.wait_for(self._look(s, [d]), FIRST_SEND_WAIT_S)
        except TimeoutError:
            log.warning("%s: know-how took too long at its first send", s.handle)

    async def _look(self, s: "Session", found: list[str]) -> None:
        st = self._state(s)
        known = {*s.know_how_sent, *st.owed, *st.empty}

        places = await asyncio.to_thread(lambda: [paths.place(p) for p in found])
        for main, _, top in places:
            if main is None or top is None or main in known:
                continue
            root = Path(top) / FOLDER
            if not self._can_read(s, root):
                continue
            known.add(main)
            docs, n = await asyncio.to_thread(read_docs, top)
            if not self._can_read(s, root):
                continue
            if not docs:
                st.empty.add(main)
            elif main not in s.know_how_sent and main not in st.owed:
                st.owed[main] = (root, block(main, top, str(s.spec.cwd), docs, n))

    def pending(self, s: "Session") -> str:
        """The menus the session is owed, marked told, joined by a blank line;
        "" when there are none."""
        st = self._seen.get(s.log_id)
        if st is None or not st.owed:
            return ""
        owed = {
            main: menu
            for main, (root, menu) in st.owed.items()
            if self._can_read(s, root)
        }
        st.owed = {}
        if not owed:
            return ""
        s.know_how_sent = [*s.know_how_sent, *owed]
        try:
            s._metas.write_soon(s.log_id, s.meta)
        except OSError:  # a full disk keeps the in-memory record (#395)
            log.warning("aegis: could not record know-how for %s", s.handle)
        return "\n\n".join(owed.values())

    def reset(self, s: "Session") -> None:
        """Its context was compacted: the next touch of a repository tells it
        again."""
        self._seen.pop(s.log_id, None)
        s.know_how_sent = []

    def drop(self, log_id: str) -> None:
        self._seen.pop(log_id, None)
