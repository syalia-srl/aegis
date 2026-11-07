"""Queues of worker sessions.

One dispatcher serves every queue, up to ``max_workers`` workers at once on
the server (config.py). A free slot goes to the queue with the fewest running
workers for its ``priority``, the smallest (running + 1) / priority, so a queue
at priority 2 runs two workers for each one of a queue at priority 1; a queue
with nothing to start leaves its share to the others. Priority 0 pauses a
queue: its tasks are kept pending and none starts. Within a queue tasks start
FIFO. A worker session is spawned from the queue's agent with the payload as
its first prompt. The worker is an ordinary session with a tab, marked
``worker``.

**A turn ending is not completion.** Ending a turn is how an agent waits, so a
task finishes only when its worker's turn has ended *and* it has no live
monitor, no held inbox message and no open Claude task (a background Bash
still running). Reading a turn boundary as done closed a worker mid-wait in
the old tree on 2026-08-10. The result is the worker's last prose; with a
callback, the enqueuer gets it in its inbox; the worker is archived.

A worker whose process exits on its own fails its task and keeps its tab.

Tasks are an append-only log, ``<state>/tasks.jsonl``, replayed at boot:
pending tasks are dispatched again, and a running task's worker is resumed
with "the server restarted; continue your task".

The queues are read from ``.aegis.yaml`` as it is at each dispatch (config.py),
so a queue added or changed on disk takes tasks with no restart, and the app
dispatches again on every change.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from .agents import ConfigError, read_config, resolve
from . import restarts
from . import notice_sources
from .monitors import iso_now
from .notice_sources import Item
from .names import default_title
from .ops import OpError

if TYPE_CHECKING:
    from .config import Config
    from .monitors import Monitors
    from .registry import Registry
    from .session import Session

log = logging.getLogger("aegis.queues")
# A queue's priority when .aegis.yaml leaves it out.
DEFAULT_PRIORITY = 1
# Queue keys read and ignored: max_parallel was each queue's own bound before
# max_workers bounded them all (#927).
LEGACY_QUEUE_KEYS = ("max_parallel",)
RESTARTED = "The server restarted while you were working on your task. Continue it where you left off."
CONTINUE = "Continue your task where you left off."


@dataclass
class Task:
    id: str
    queue: str
    payload: str
    callback: bool
    enqueuer: str | None  # a log id, or None for a person
    cwd: str
    status: str = "pending"  # pending | running | completed | failed | cancelled
    worker: str | None = None
    result: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    # The principal the task and its worker belong to (tokens.py); None in a
    # log from before owners, which reads as the admin's.
    owner: str | None = None
    # The agent on a server linked with trust that enqueued it, as
    # {handle, server}: its result goes back over the link (#684).
    far_enqueuer: dict | None = None

    def public(self) -> dict:
        d = asdict(self)
        d["position"] = None
        return d


# A session's enqueued tasks not finished, as one status line (notices.py).
# A task's result reaches its enqueuer through the inbox, so no warning.
SOURCE = "queues"
notice_sources.declare(SOURCE, {})


def queue_items(running: list[str], waiting: int) -> list[Item]:
    """``running``: the workers' handles, in enqueue order."""
    parts = []
    if running:
        parts.append(f"{len(running)} running ({', '.join(running)})")
    if waiting:
        parts.append(f"{waiting} waiting")
    return (
        [Item("out", "info", "queue tasks out: " + ", ".join(parts))] if parts else []
    )


def queues_from(raw: Any) -> dict[str, dict]:
    """Every queue in a parsed ``queues:`` map. A queue that does not name its
    agent, or whose ``priority`` is not a whole number from 0 up, is kept with
    an ``error``, so enqueueing on it says what is wrong. ``priority`` left out
    is 1. ``max_parallel``, the per-queue bound before #927, is read and
    ignored, so a file from then still runs; ``legacy`` names it for aegis
    doctor."""
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict] = {}
    for name, q in raw.items():
        q = q if isinstance(q, dict) else {}
        agent, priority = q.get("agent"), q.get("priority", DEFAULT_PRIORITY)
        if agent in (None, ""):
            out[str(name)] = {"error": "agent is missing"}
        elif (
            isinstance(priority, bool) or not isinstance(priority, int) or priority < 0
        ):
            out[str(name)] = {
                "error": f"priority {priority!r} is not a whole number from 0 up"
            }
        elif not isinstance(q.get("quiet", True), bool):
            out[str(name)] = {"error": f"quiet {q['quiet']!r} is not true or false"}
        else:
            # A worker starts with no tab: its result goes to its enqueuer (#386).
            out[str(name)] = {
                "agent": str(agent),
                "priority": priority,
                "quiet": q.get("quiet", True),
                "legacy": [k for k in LEGACY_QUEUE_KEYS if k in q],
            }
    return out


def load_queues(config_root: Path) -> dict[str, dict]:
    try:
        raw = read_config(config_root).get("queues")
    except ConfigError:
        return {}
    return queues_from(raw)


class Queues:
    def __init__(
        self, registry: Registry, monitors: Monitors, path: Path, config: Config
    ) -> None:
        # Sends a task's result back to an agent on a server linked with
        # trust (app.py sets it).
        self.far_callback: Callable[[Task, str, str], Awaitable[None]] | None = None
        self._registry = registry
        self._monitors = monitors
        self._path = path
        self._config = config
        self.tasks: dict[str, Task] = {}
        self._dispatching = False
        self._again = False
        # The grace checks after a worker's SIGTERM, cancelled by a stop.
        self._sigterm_timers: set[asyncio.TimerHandle] = set()

    def shutdown(self) -> None:
        """The server is stopping: a SIGTERM grace check must not outlive it."""
        for h in self._sigterm_timers:
            h.cancel()
        self._sigterm_timers.clear()

    @property
    def queues(self) -> dict[str, dict]:
        """The queues in .aegis.yaml as it is now (config.py)."""
        return self._config.current().queues

    # -- the log --------------------------------------------------------------
    def _log(self, t: Task, event: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            f.write(json.dumps({"event": event, "at": time.time(), **asdict(t)}) + "\n")
        if t.enqueuer:
            self._status(t.enqueuer)

    def _status(self, enqueuer: str) -> None:
        """Set the enqueuer's queue notice from its tasks not finished."""
        notices = self._registry.notices
        if notices is None:
            return
        mine = sorted(
            (t for t in self.tasks.values() if t.enqueuer == enqueuer),
            key=lambda t: t.created_at,
        )
        running = [
            s.handle
            for t in mine
            if t.status == "running"
            and (s := self._registry.sessions.get(t.worker or "")) is not None
        ]
        waiting = sum(1 for t in mine if t.status == "pending")
        on = notices.cfg(SOURCE) is not None
        notices.set(enqueuer, SOURCE, queue_items(running, waiting) if on else [])

    def boot(self) -> list[Task]:
        """Replay the log; return the running tasks whose workers must resume."""
        if self._path.exists():
            for raw in self._path.open(errors="replace"):
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                rec.pop("event", None)
                rec.pop("at", None)
                try:
                    t = Task(**rec)
                except TypeError:
                    continue
                self.tasks[t.id] = t
        resume = []
        for t in self.tasks.values():
            if t.status == "running":
                if t.worker in self._registry.sessions:
                    resume.append(t)
                else:
                    self._fail(t, "its worker was gone after a restart")
        for enqueuer in {t.enqueuer for t in self.tasks.values() if t.enqueuer}:
            self._status(enqueuer)
        return resume

    # -- position and status ------------------------------------------------------
    def position(self, t: Task) -> int | None:
        if t.status != "pending":
            return None
        pending = [
            x
            for x in self.tasks.values()
            if x.queue == t.queue and x.status == "pending"
        ]
        pending.sort(key=lambda x: x.created_at)
        return pending.index(t) + 1

    def status(self, t: Task) -> dict:
        d = t.public()
        d["position"] = self.position(t)
        q = self.queues.get(t.queue)
        if t.status == "pending" and q is not None and "error" not in q:
            d["waiting_for_quota"] = self.quota_wait(t, q)
            d["paused"] = q["priority"] == 0
        if t.worker and t.worker in self._registry.sessions:
            d["worker_handle"] = self._registry.sessions[t.worker].handle
        return d

    def task_of_worker(self, log_id: str) -> Task | None:
        return next(
            (
                t
                for t in self.tasks.values()
                if t.worker == log_id and t.status == "running"
            ),
            None,
        )

    # -- operations ------------------------------------------------------------------
    async def enqueue(
        self,
        queue: str,
        payload: str,
        callback: bool,
        enqueuer: str | None,
        cwd: str,
        owner: str | None = None,
        far_enqueuer: dict | None = None,
    ) -> Task:
        t = Task(
            id=f"task-{secrets.token_hex(4)}",
            queue=queue,
            payload=payload,
            callback=callback,
            enqueuer=enqueuer,
            cwd=cwd,
            owner=owner,
            far_enqueuer=far_enqueuer,
        )
        self.tasks[t.id] = t
        self._log(t, "enqueued")
        await self.dispatch()
        return t

    async def dispatch(self) -> None:
        """Start what the queues allow. A call that arrives while a dispatch is
        running (a config change, a task finishing) makes that dispatch run
        again, rather than being dropped: its loop read the old limits."""
        if self._dispatching:
            self._again = True
            return
        self._dispatching = True
        try:
            while True:
                self._again = False
                await self._dispatch_once()
                if not self._again:
                    break
        finally:
            self._dispatching = False

    async def _dispatch_once(self) -> None:
        # A task logged on a queue that has since lost a field, or been
        # removed, would wait forever for a slot; fail it with the reason.
        # A moment with no file on disk is an editor saving (Vim and Emacs
        # rename the old file away, a git checkout unlinks it), not a decision
        # to drop the backlog: tasks wait for the file to come back.
        present = self._config.current().exists
        for t in list(self.tasks.values()):
            q = self.queues.get(t.queue)
            if present and t.status == "pending" and (q is None or "error" in q):
                why = q["error"] if q else "it is no longer configured"
                self._fail(t, f"its queue {t.queue!r} in .aegis.yaml: {why}")
        limit = self._config.current().max_workers
        while True:
            running = Counter(
                t.queue for t in self.tasks.values() if t.status == "running"
            )
            if sum(running.values()) >= limit:
                return
            best: tuple[tuple[float, float], Task, dict] | None = None
            for name, q in self.queues.items():
                if "error" in q or q["priority"] < 1:
                    continue
                head = min(
                    (
                        t
                        for t in self.tasks.values()
                        if t.queue == name
                        and t.status == "pending"
                        and self.quota_wait(t, q) is None
                    ),
                    key=lambda t: t.created_at,
                    default=None,
                )
                if head is None:
                    continue
                key = ((running[name] + 1) / q["priority"], head.created_at)
                if best is None or key < best[0]:
                    best = (key, head, q)
            if best is None:
                return
            await self._start(best[1], best[2])

    def quota_wait(self, t: Task, q: dict) -> str | None:
        """Why ``t``'s owner's quota gate keeps it pending, or None. A task
        that waits is skipped, never failed, so the next owner's starts; the
        gate's next reading dispatches it (app.on_quota). An agent that cannot
        resolve is left to _start, which fails the task with the reason."""
        start_spec = self._registry.start_spec
        if start_spec is None:
            return None
        try:
            agents = list(self._config.current().agents)
            spec = resolve(agents, None, q["agent"], {}, Path(t.cwd), owner=t.owner)
        except OpError:
            return None
        _, g = start_spec(spec)
        return None if g.may_start else g.why

    async def _start(self, t: Task, q: dict) -> None:
        try:
            agents = list(self._config.current().agents)
            # A worker joins its enqueuer's group (#586).
            enqueuer = self._registry.sessions.get(t.enqueuer or "")
            group = enqueuer.group_key() if enqueuer else None
            spec = resolve(
                agents, None, q["agent"], {}, Path(t.cwd), owner=t.owner, group=group
            )
        except OpError as e:
            self._fail(t, f"the queue's agent {q['agent']!r} cannot start: {e.message}")
            return
        if self._registry.start_spec is not None:
            spec, _ = self._registry.start_spec(spec)
        try:
            s = await self._registry.spawn(
                spec,
                worker={"task_id": t.id, "queue": t.queue},
                title=default_title(t.payload),
                quiet=q["quiet"],
            )
        except Exception as e:
            self._fail(t, f"its worker could not start: {e}")
            return
        t.status, t.worker = "running", s.log_id
        self._log(t, "dispatched")
        if t.enqueuer:
            self._registry.refresh_card(t.enqueuer)  # it names the worker now
        await s.send(t.payload)

    async def cancel(self, t: Task) -> None:
        if t.status == "pending":
            t.status, t.finished_at = "cancelled", time.time()
            self._log(t, "cancelled")
        elif t.status == "running":
            t.status, t.finished_at = "cancelled", time.time()
            self._log(t, "cancelled")
            if t.worker in self._registry.sessions:
                await self._registry.close(t.worker)
        if t.enqueuer:
            self._registry.refresh_card(t.enqueuer)
        await self.dispatch()

    async def resume(self, t: Task) -> None:
        s = self._registry.sessions.get(t.worker or "")
        if s is None:
            if t.worker in self._registry.archived:
                s = self._registry.reopen(t.worker)
            else:
                raise LookupError("its worker no longer exists")
        t.status, t.error, t.finished_at = "running", None, None
        self._log(t, "resumed")
        self._registry.refresh_card(s.log_id)
        await s.deliver(
            f"> from queue:{t.queue} · task#{t.id} · resume · {iso_now()}", CONTINUE
        )

    async def resume_after_boot(self, tasks: list[Task]) -> None:
        for t in tasks:
            s = self._registry.sessions.get(t.worker or "")
            if s is None:
                self._fail(t, "its worker was gone after a restart")
                continue
            if s.log_id in self._registry.declined:
                self._fail(
                    t, f"its worker's turn was not continued: {restarts.DECLINED}"
                )
                continue
            if s.log_id in self._registry.continuing:
                continue  # the registry already told it the server restarted
            try:
                await s.deliver(
                    f"> from queue:{t.queue} · task#{t.id} · restart · {iso_now()}",
                    RESTARTED,
                )
            except Exception as e:
                self._fail(t, f"its worker could not resume after a restart: {e}")
        await self.dispatch()

    # -- completion ----------------------------------------------------------------
    def finished(self, s: Session) -> bool:
        return self._registry.finished(s)

    def turn_ended(self, s: Session) -> None:
        t = self.task_of_worker(s.log_id)
        if t is None or not self.finished(s):
            return
        asyncio.get_running_loop().create_task(self._complete(t, s))

    async def _complete(self, t: Task, s: Session) -> None:
        if t.status != "running" or not self.finished(s):
            return
        prose = [e["md"] for e in s.entries() if e["kind"] == "prose" and e.get("md")]
        t.status, t.result, t.finished_at = (
            "completed",
            prose[-1] if prose else "",
            time.time(),
        )
        self._log(t, "completed")
        # Archive first, so the enqueuer reacting to its callback finds the
        # worker already put away.
        try:
            await self._registry.close(s.log_id)
        except Exception:
            log.exception("aegis: could not archive worker %s", s.handle)
        await self._callback(t, "ok", t.result or "(the worker said nothing)")
        await self.dispatch()

    def exited(self, s: Session, code: int, stderr_tail: list[str]) -> None:
        t = self.task_of_worker(s.log_id)
        if t is None:
            return
        if code in restarts.SIGTERM_CODES:
            # Most likely the server's own stop under systemd's control-group
            # kill, a moment before aegis itself: the next boot continues the
            # worker (restarts.py), so the task stays running.
            log.info(
                "aegis: worker %s got SIGTERM; its task waits for the next boot",
                s.handle,
            )
            self._sigterm_timers.add(
                asyncio.get_running_loop().call_later(
                    restarts.GRACE_S, self._after_sigterm, t.id, s.log_id
                )
            )
            return
        tail = "\n".join(stderr_tail[-10:])
        self._fail(
            t,
            f"its worker {s.handle} exited with code {code}"
            + (f":\n{tail}" if tail else ""),
        )

    def _after_sigterm(self, task_id: str, log_id: str) -> None:
        """The grace after a worker's SIGTERM is over and this server is still
        up, so no restart caused it: the task fails as for any other exit. A
        stop in progress cancels this with the loop."""
        t = self.tasks.get(task_id)
        if t is None or t.status != "running" or t.worker != log_id:
            return
        s = self._registry.sessions.get(log_id)
        if s is not None and s.running:
            return
        name = s.handle if s is not None else log_id
        self._fail(t, f"its worker {name} got SIGTERM and no restart followed")

    def _fail(self, t: Task, why: str) -> None:
        t.status, t.error, t.finished_at = "failed", why, time.time()
        self._log(t, "failed")
        loop = asyncio.get_running_loop()
        loop.create_task(self._callback(t, "error", f"The task failed: {why}"))
        loop.create_task(self.dispatch())

    async def _callback(self, t: Task, outcome: str, body: str) -> None:
        if t.callback and t.far_enqueuer and self.far_callback is not None:
            try:
                await self.far_callback(t, outcome, body)
            except Exception:
                log.exception("aegis: could not send the callback of %s back", t.id)
            return
        if not t.callback or not t.enqueuer:
            return
        s = self._registry.sessions.get(t.enqueuer)
        if s is None:
            return
        try:
            # The enqueuer's own task: it reaches a workflow node too.
            await s.deliver(
                f"> from queue:{t.queue} · task#{t.id} · {outcome} · {iso_now()}",
                body,
                through_lock=True,
            )
        except Exception:
            log.exception("aegis: could not deliver the callback of %s", t.id)
