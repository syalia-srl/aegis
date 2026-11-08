"""Feeds: a source that grows wakes the session that started it with each
batch of new items (docs/superpowers/specs/2026-11-08-feeds-design.html).

A feed is a file of lines or a command with a cursor. aegis polls it, keeps
what it read and has not delivered as ``pending``, and hands a batch to
``Session.deliver`` under ``> from feed:<name> · batch#<n> · …``. The feed's
position and its pending items are saved after the hand-off, so a crash in
between sends the batch again under the same number (at least once): a
repeat the agent can see beats an item lost in silence.

When a batch goes:

- idle or asleep session: after ``debounce_s`` with no new item, and never
  more than ``DEBOUNCE_MAX_S`` after the oldest pending one;
- working session: never mid-turn. Its turn's end (``turn_ended``) adds one
  block per feed to the held flush, so ten bursts during one turn arrive as
  one block. Meanwhile a ``feeds`` notice tells the agent items wait;
- at most ``max_items`` items or ``BATCH_BYTES`` per batch; the rest goes when
  the turn that batch started ends.

A file source keeps a byte offset and the file's inode; only lines ending in a
newline count, and a file that shrinks or changes inode is read from its
start. A jsonl file may carry a ``jq`` filter: what it prints for a line is the
item, and a line it prints nothing for is dropped. A command source gets its
cursor in ``$AEGIS_FEED_CURSOR``, prints one item per line, writes the next
cursor to ``$AEGIS_FEED_NEXT``, and exits 0 (items), 1 (nothing new) or
anything else (an error). Three failed polls in a row pause the feed and tell
the agent. A command runs through ``monitors.run_bash``, as a monitor's
checks do.

Feeds live in ``<state>/feeds.json`` and are armed again at boot. Closing the
session drops them; a handoff on this server moves them to its successor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from . import launch, notice_sources
from .monitors import iso_now, run_bash
from .notice_sources import Item

if TYPE_CHECKING:
    from .registry import Registry
    from .session import Session

log = logging.getLogger("aegis.feeds")

MAX_PER_SESSION = 5
DEBOUNCE_MAX_S = 120.0
BATCH_BYTES = 32 * 1024
# What a feed keeps between reading and delivering, oldest dropped past it.
PENDING_ITEMS = 1000
PENDING_BYTES = 1024 * 1024
# What one poll of a file reads at most; the rest comes on the next poll.
READ_BYTES = 1024 * 1024
ERRORS_TO_PAUSE = 3
JQ_TIMEOUT_S = 10.0
RECENT_KEPT = 12
ITEM_SHOWN = 160

SOURCE = "feeds"
notice_sources.declare(SOURCE, {"wake": True})


@dataclass
class Feed:
    name: str
    owner: str  # log id
    cwd: str
    # {"file": path, "format": "lines"|"jsonl", "filter": str|None} or
    # {"command": str}; plus "interval_s".
    source: dict
    debounce_s: float = 15.0
    max_items: int = 50
    started_at: float = field(default_factory=time.time)
    paused: str | None = None  # why, when paused: "by you", "3 failed polls: …"
    # The source's position: a file's {"inode", "offset", "line"}, a
    # command's {"cursor"}.
    pos: dict = field(default_factory=dict)
    pending: list[dict] = field(default_factory=list)  # {"text", "seen"}
    batch: int = 0
    delivered: int = 0
    filtered: int = 0
    dropped: int = 0  # past PENDING_ITEMS or PENDING_BYTES
    last_new: float | None = None  # when a poll last added an item
    # The batch number handed to deliver and not yet saved as delivered: a
    # restart that finds it set sends that batch again, marked so.
    sending: int | None = None
    errors: int = 0
    last_error: str = ""
    polled_at: float | None = None
    notes: list[str] = field(default_factory=list)  # told in the next batch
    recent: list[dict] = field(default_factory=list)  # {"text","seen","sent","batch"}

    @property
    def kind(self) -> str:
        return "file" if "file" in self.source else "command"

    @property
    def interval_s(self) -> float:
        return float(
            self.source.get("interval_s") or (5 if self.kind == "file" else 30)
        )

    def where(self) -> str:
        if self.kind == "file":
            return f"{self.source['file']} ({self.source.get('format', 'lines')})"
        return f"command {self.source['command']}"

    def state(self, session: Session | None) -> str:
        if self.paused:
            return "paused"
        if not self.pending:
            return "live"
        if session is not None and session.in_turn:
            return "waits for the turn"
        return "debouncing"

    def due(self, now: float) -> bool:
        """The debounce passed: quiet for debounce_s, or the oldest pending
        item has waited DEBOUNCE_MAX_S."""
        if not self.pending or self.paused:
            return False
        quiet = now - (self.last_new or now) >= self.debounce_s
        return quiet or now - self.pending[0]["seen"] >= DEBOUNCE_MAX_S

    def card(self, session: Session | None) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "where": self.where(),
            "source": self.source,
            "cwd": self.cwd,
            "state": self.state(session),
            "paused": self.paused,
            "started_at": self.started_at,
            "interval_s": self.interval_s,
            "debounce_s": self.debounce_s,
            "max_items": self.max_items,
            "pending": len(self.pending),
            "oldest_pending": self.pending[0]["seen"] if self.pending else None,
            "last_new": self.last_new,
            "batch": self.batch,
            "delivered": self.delivered,
            "filtered": self.filtered,
            "dropped": self.dropped,
            "errors": self.errors,
            "last_error": self.last_error,
            "polled_at": self.polled_at,
            "pos": self.pos,
            "recent": self.recent,
        }


def batch_text(f: Feed, items: list[dict], now: float, again: bool) -> tuple[str, str]:
    """The header and body of the batch ``items`` of ``f``."""
    n = len(items)
    num = f"batch#{f.batch}" + (" again" if again else "")
    header = f"> from feed:{f.name} · {num} · {n} item{'s' * (n != 1)} · {iso_now()}"
    oldest = now - min(i["seen"] for i in items)
    lines = [
        f"{n} new item{'s' * (n != 1)} from {f.where()}, the oldest seen "
        f"{_ago(oldest)} ago."
    ]
    lines += f.notes
    fence = "jsonl" if f.source.get("format") == "jsonl" else "text"
    lines += [f"```{fence}", *(i["text"] for i in items), "```"]
    rest = len(f.pending)
    if rest:
        lines.append(
            f"{rest} more item{'s' * (rest != 1)} wait{'s' * (rest == 1)} and "
            "come when this turn ends."
        )
    else:
        lines.append("Nothing else waits.")
    lines.append(
        f"feed_pause or feed_cancel with name={f.name} stops it. Act on the items "
        "and end your turn; the next batch wakes you."
    )
    return header, "\n".join(lines)


def _ago(seconds: float) -> str:
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m}m {s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"


def take(pending: list[dict], cap: int) -> list[dict]:
    """The first items of ``pending`` that fit ``cap`` and BATCH_BYTES; at
    least one, however long."""
    out, size = [], 0
    for it in pending[:cap]:
        size += len(it["text"].encode()) + 1
        if out and size > BATCH_BYTES:
            break
        out.append(it)
    return out


def split_lines(data: bytes) -> tuple[list[str], int]:
    """The complete lines in ``data`` and how many bytes they take: a line
    without its newline yet waits for the next read."""
    end = data.rfind(b"\n")
    if end < 0:
        return [], 0
    return data[:end].decode("utf-8", "replace").split("\n"), end + 1


class SourceError(Exception):
    """A poll that failed: its one-line reason."""


class Feeds:
    def __init__(self, registry: Registry, path: Path) -> None:
        self._registry = registry
        self._path = path
        self.items: dict[tuple[str, str], Feed] = {}  # (owner, name) -> feed
        self._tasks: dict[tuple[str, str], asyncio.Task] = {}
        self._kick: dict[tuple[str, str], asyncio.Event] = {}
        # Overridable in tests: how a command or a read runs.
        self.run: Callable[..., Awaitable[tuple[int | None, str, str]]] = run_bash

    # -- persistence ---------------------------------------------------------
    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps([asdict(f) for f in self.items.values()]))
        os.replace(tmp, self._path)

    def boot(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            log.warning("aegis: %s is damaged; starting with no feeds", self._path)
            return
        for d in raw if isinstance(raw, list) else []:
            try:
                f = Feed(**d)
            except TypeError:
                continue
            if f.owner in self._registry.sessions:
                self.items[(f.owner, f.name)] = f
        self._save()

    def arm_all(self) -> None:
        for f in self.items.values():
            self._arm(f)
        for owner in {f.owner for f in self.items.values()}:
            self._status(owner)

    async def shutdown(self) -> None:
        for t in self._tasks.values():
            t.cancel()
        self._tasks.clear()

    # -- operations ----------------------------------------------------------
    def of(self, owner: str) -> list[Feed]:
        return [f for f in self.items.values() if f.owner == owner]

    def live(self, owner: str) -> list[Feed]:
        return [f for f in self.of(owner) if not f.paused]

    def get(self, owner: str, name: str) -> Feed | None:
        return self.items.get((owner, name))

    def card(self, owner: str) -> list[dict]:
        s = self._registry.sessions.get(owner)
        return [f.card(s) for f in self.of(owner)]

    async def start(self, owner: str, cwd: str, name: str, source: dict, **kw) -> Feed:
        """A new feed, positioned by ``source["from"]`` for a file; its first
        poll has run when this returns. Raises SourceError when the source
        cannot be read at all."""
        f = Feed(name=name, owner=owner, cwd=cwd, source=dict(source), **kw)
        start = f.source.pop("from", "end")
        if f.kind == "file":
            inode, size, data = await self._read(f, 0, whole=start != "end")
            if start == "end":
                f.pos = {"inode": inode, "offset": size, "line": None}
            else:
                skip = start.get("line", 0) if isinstance(start, dict) else 0
                lines, used = split_lines(data)
                skip = min(skip, len(lines))
                offset = sum(len(ln.encode()) + 1 for ln in lines[:skip])
                f.pos = {"inode": inode, "offset": offset, "line": skip}
                if skip == len(lines):
                    f.pos["offset"] = used
        self.items[(owner, name)] = f
        try:
            await self._poll(f)
        except SourceError as e:
            if f.kind == "file":
                del self.items[(owner, name)]
                raise
            f.errors, f.last_error = 1, str(e)
        self._save()
        self._arm(f)
        self._changed(f)
        return f

    def cancel(self, owner: str, name: str) -> Feed | None:
        f = self.items.pop((owner, name), None)
        if f is None:
            return None
        t = self._tasks.pop((owner, name), None)
        if t:
            t.cancel()
        self._kick.pop((owner, name), None)
        self._save()
        self._changed(f)
        return f

    def pause(self, f: Feed, why: str) -> None:
        f.paused = why
        self._save()
        self._changed(f)

    def resume(self, f: Feed) -> None:
        f.paused, f.errors, f.last_error = None, 0, ""
        self._save()
        self._changed(f)
        self._wake(f)

    def send_now(self, f: Feed) -> str:
        """Skip the debounce: deliver what waits now, or when the turn ends."""
        if not f.pending:
            return "nothing waits"
        s = self._registry.sessions.get(f.owner)
        if s is not None and s.in_turn:
            return "they go when the turn ends"
        f.last_new = 0.0
        self._wake(f)
        return "sent"

    def drop_owner(self, owner: str) -> None:
        for f in self.of(owner):
            self.cancel(owner, f.name)

    def move(self, old: str, new: str) -> None:
        """A handoff: the feeds of ``old`` follow its successor ``new``."""
        for f in self.of(old):
            t = self._tasks.pop((old, f.name), None)
            if t:
                t.cancel()
            self._kick.pop((old, f.name), None)
            del self.items[(old, f.name)]
            f.owner = new
            self.items[(new, f.name)] = f
            self._arm(f)
        self._save()
        self._registry.refresh_card(old)
        self._status(old)
        self._registry.refresh_card(new)
        self._status(new)

    def turn_ended(self, s: Session) -> None:
        """The turn ended: each feed's pending items join the held flush as one
        block, or go now when nothing is held. Synchronous, so the block is in
        ``s.held`` before the flush task the turn's end scheduled takes it."""
        for f in self.live(s.log_id):
            if f.pending:
                self._deliver(f, s)

    # -- delivery ------------------------------------------------------------
    def _deliver(self, f: Feed, s: Session) -> None:
        items = take(f.pending, f.max_items)
        if not items:
            return
        again = f.sending == f.batch + 1
        f.sending = f.batch + 1
        self._save()
        f.batch += 1
        f.pending = f.pending[len(items) :]
        header, body = batch_text(f, items, time.time(), again)
        f.notes = []
        t = time.time()
        f.delivered += len(items)
        f.recent = [
            *({"text": i["text"][:ITEM_SHOWN], "seen": i["seen"], "sent": t,
               "batch": f.batch} for i in reversed(items)),
            *f.recent,
        ][:RECENT_KEPT]  # fmt: skip
        if s.held or s.in_turn:
            # A flush is pending (the turn just ended with messages held) or
            # a turn runs: hold it with them.
            quota = next((m["held_for"] for m in s.held if "held_for" in m), None)
            msg = {"header": header, "body": body, "ts": t}
            s._hold(msg | ({"held_for": quota} if quota else {}))
        else:
            task = asyncio.get_running_loop().create_task(
                s.deliver(header, body, through_lock=True)
            )
            task.add_done_callback(_log_failure(f))
        f.sending = None
        self._save()
        self._changed(f)

    # -- polling -------------------------------------------------------------
    def _arm(self, f: Feed) -> None:
        key = (f.owner, f.name)
        self._kick[key] = asyncio.Event()
        self._tasks[key] = asyncio.get_running_loop().create_task(self._watch(f))

    def _wake(self, f: Feed) -> None:
        if (ev := self._kick.get((f.owner, f.name))) is not None:
            ev.set()

    async def _watch(self, f: Feed) -> None:
        key = (f.owner, f.name)
        while True:
            try:
                await self._tick(f)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("aegis: feed %s of %s failed a poll", f.name, f.owner)
            wait = f.interval_s
            if f.pending and not f.paused:
                left = f.debounce_s - (time.time() - (f.last_new or 0))
                wait = max(0.2, min(wait, left))
            ev = self._kick.get(key)
            if ev is None:
                return
            try:
                await asyncio.wait_for(ev.wait(), wait)
            except TimeoutError:
                pass
            ev.clear()

    async def _tick(self, f: Feed) -> None:
        before = (len(f.pending), f.errors, f.paused)
        try:
            await self._poll(f)
        except SourceError as e:
            f.errors += 1
            f.last_error = str(e)
            if f.errors >= ERRORS_TO_PAUSE and not f.paused:
                f.paused = f"{ERRORS_TO_PAUSE} failed polls in a row: {e}"
        else:
            if f.errors:
                f.errors, f.last_error = 0, ""
        s = self._registry.sessions.get(f.owner)
        if s is not None and f.due(time.time()) and not s.in_turn and not s.held:
            self._deliver(f, s)
        elif (len(f.pending), f.errors, f.paused) != before:
            self._save()
            self._changed(f)

    async def _poll(self, f: Feed) -> None:
        f.polled_at = time.time()
        texts = await (
            self._poll_file(f) if f.kind == "file" else self._poll_command(f)
        )
        if not texts:
            return
        now = time.time()
        f.pending += [{"text": t, "seen": now} for t in texts]
        f.last_new = now
        over = len(f.pending) - PENDING_ITEMS
        size = sum(len(i["text"]) for i in f.pending)
        while f.pending and (over > 0 or size > PENDING_BYTES):
            size -= len(f.pending.pop(0)["text"])
            over -= 1
            f.dropped += 1
            f.notes = [
                f"{f.dropped} older items were dropped: more waited than a feed keeps."
            ]

    async def _poll_file(self, f: Feed) -> list[str]:
        pos = f.pos
        inode, size, data = await self._read(f, pos.get("offset", 0))
        if inode != pos.get("inode") or size < pos.get("offset", 0):
            f.notes.append(
                "The file was replaced or truncated; it is read from its start."
            )
            f.pos = pos = {"inode": inode, "offset": 0, "line": 0}
            inode, size, data = await self._read(f, 0)
        lines, used = split_lines(data)
        if not used:
            return []
        pos["offset"] += used
        if pos.get("line") is not None:
            pos["line"] += len(lines)
        if f.source.get("format") == "jsonl":
            return await self._jsonl(f, lines)
        return [ln for ln in lines if ln.strip()]

    async def _jsonl(self, f: Feed, lines: list[str]) -> list[str]:
        """Each line compacted, or run through the feed's filter: what it
        prints for a line is that line's items. A line that does not parse
        is kept as it is, unfiltered."""
        parsed: list[tuple[str, object | None]] = []
        for ln in lines:
            if not ln.strip():
                continue
            try:
                parsed.append((ln, json.loads(ln)))
            except ValueError:
                parsed.append((ln, None))
        flt = f.source.get("filter")
        if not flt:
            return [ln for ln, _ in parsed]
        good = [v for _, v in parsed if v is not None]
        outs = iter(await run_jq(flt, good) if good else [])
        items = []
        for ln, v in parsed:
            if v is None:
                items.append(ln)
                continue
            got = next(outs)
            if not got:
                f.filtered += 1
            items += [
                json.dumps(o, ensure_ascii=False, separators=(",", ":")) for o in got
            ]
        return items

    async def _read(
        self, f: Feed, offset: int, whole: bool = False
    ) -> tuple[int, int, bytes]:
        """The file's inode and size, and up to READ_BYTES from ``offset``
        (64 times that when ``whole``, to find a starting line)."""
        path = self._expand(f)
        limit = 64 * READ_BYTES if whole else READ_BYTES
        try:
            st = os.stat(path)
            with open(path, "rb") as fh:
                fh.seek(min(offset, st.st_size))
                return st.st_ino, st.st_size, fh.read(limit)
        except OSError as e:
            raise SourceError(f"{e.strerror}: {path}") from None

    async def _poll_command(self, f: Feed) -> list[str]:
        rc, out, err = await self.run(command_script(f), f.cwd)
        out, sep, new = out.rpartition(CURSOR_MARK)
        if not sep:
            out, new = new, None
        else:
            new = new.strip()
        if rc == 1:
            return []
        if rc != 0:
            why = (err.strip().splitlines() or [""])[-1]
            raise SourceError(f"exit {rc}" + (f": {why}" if why else ""))
        items = [ln for ln in out.splitlines() if ln.strip()]
        if new is None:
            if items:
                raise SourceError(
                    "it printed items but wrote no next cursor to $AEGIS_FEED_NEXT"
                )
            return []
        f.pos["cursor"] = new
        return items

    def _expand(self, f: Feed) -> Path:
        p = Path(f.source["file"]).expanduser()
        return p if p.is_absolute() else Path(f.cwd) / p

    # -- the card and notices ------------------------------------------------
    def _changed(self, f: Feed) -> None:
        self._registry.refresh_card(f.owner)
        self._status(f.owner)

    def session_changed(self, owner: str) -> None:
        """The session's status changed: its "items wait" notices follow it."""
        if self.of(owner):
            self._status(owner)

    def _status(self, owner: str) -> None:
        """The owner's notices: a warning per feed whose items wait for its
        turn to end, and one per feed paused by errors."""
        notices = self._registry.notices
        if notices is None:
            return
        cfg = notices.cfg(SOURCE)
        s = self._registry.sessions.get(owner)
        items = []
        for f in self.of(owner) if cfg is not None else []:
            if f.pending and not f.paused and s is not None and s.in_turn:
                n = len(f.pending)
                text = (
                    f"feed:{f.name} · {n} item{'s' * (n != 1)} wait for your turn "
                    "to end. End your turn early if they may change your work."
                )
                items.append(Item(f"{f.name}:waiting", "warn", text))
            if f.paused and f.errors >= ERRORS_TO_PAUSE:
                text = f"feed {f.name} paused: {f.paused} · fix it and feed_resume, or feed_cancel"
                items.append(Item(f"{f.name}:broken", "warn", text, wake=cfg["wake"]))
        notices.set(owner, SOURCE, items)


# What separates a command's stdout from the cursor it wrote (command_script).
CURSOR_MARK = "\x1eaegis-feed-next\x1e"


def command_script(f: Feed) -> str:
    """The bash that runs ``f``'s command with its cursor in
    ``$AEGIS_FEED_CURSOR`` and a fresh path in ``$AEGIS_FEED_NEXT``, and
    prints CURSOR_MARK and that file's text after the command's stdout when
    the command wrote it. Its exit code is the command's."""
    cursor = shlex.quote(f.pos.get("cursor") or "")
    return (
        'AEGIS_FEED_NEXT="$(mktemp -u)"; '
        f"export AEGIS_FEED_CURSOR={cursor} AEGIS_FEED_NEXT; "
        f"( {f.source['command']}\n ); rc=$?; "
        'if [ -e "$AEGIS_FEED_NEXT" ]; then '
        f'printf \'{CURSOR_MARK}\'; cat "$AEGIS_FEED_NEXT"; rm -f "$AEGIS_FEED_NEXT"; fi; '
        "exit $rc"
    )


async def run_jq(expr: str, values: list) -> list[list]:
    """Each of ``values`` through ``jq`` ``expr``: the list of what it printed
    for each. Raises SourceError when jq is missing or fails."""
    prog = f"[inputs | [({expr})]]"
    try:
        proc = await launch.child(
            ["jq", "-nc", prog],
            cwd="/",
            stdin=launch.PIPE,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            via=launch.resolve_via(),
        )
    except OSError as e:
        raise SourceError(f"jq cannot run: {e}") from None
    data = "\n".join(json.dumps(v) for v in values).encode()
    try:
        out, err = await asyncio.wait_for(proc.communicate(data), JQ_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise SourceError(f"the filter took over {JQ_TIMEOUT_S:g}s") from None
    if proc.returncode != 0:
        line = (err.decode("utf-8", "replace").strip().splitlines() or [""])[-1]
        raise SourceError(f"the filter failed: {line}")
    got = json.loads(out)
    if len(got) != len(values):
        raise SourceError("the filter did not answer once per line")
    return got


async def check_jq(expr: str) -> str | None:
    """Why ``expr`` cannot filter, or None when it compiles."""
    try:
        await run_jq(expr, [])
    except SourceError as e:
        return str(e)
    return None


def _log_failure(f: Feed) -> Callable[[asyncio.Task], None]:
    def done(t: asyncio.Task) -> None:
        if not t.cancelled() and (e := t.exception()) is not None:
            log.warning(
                "aegis: feed %s could not deliver batch#%d: %s", f.name, f.batch, e
            )

    return done
