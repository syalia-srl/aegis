"""A group's generated name and title (#1047): what its members work on, in a
kebab-case name and one line, written again each time a member joins or leaves.

The registry calls ``changed`` with a group's key whenever its open members
change. Nothing happens unless ``.aegis.yaml`` names an agent under
``group_names: {agent: <name>}`` (nothing defaults, agents.py). The call waits
``DEBOUNCE_S`` so a queue batch joining at once costs one call, skips a
membership it has already named, and runs through the one-shot path the recap
and /btw share (oneshot.py). A name a person or an agent chose is pinned
(group_store.py): the call then writes the title only.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re

from pydantic import BaseModel, Field

from . import oneshot

log = logging.getLogger(__name__)

DEBOUNCE_S = 15.0
TIMEOUT_S = 60
TITLE_MAX = 80
PLAN_ITEMS = 5
NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+){1,2}$")


class GroupNameOut(BaseModel):
    name: str = Field(
        description="Two or three lowercase words joined by hyphens, naming the "
        "shared work, such as docs-sweep or billing-api-fix."
    )
    title: str = Field(
        description=f"One line of at most {TITLE_MAX} characters: what the group "
        "is working on now."
    )


SYSTEM = (
    "You name a group of coding agent sessions that work together, from what "
    "each member is doing. `name` is two or three lowercase English words "
    "joined by hyphens, naming the shared goal, not any one member (never a "
    "member's handle). `title` is one plain line, at most "
    f"{TITLE_MAX} characters, saying what the group works on now, in the "
    "language the members' titles and plans are written in. No preamble, no "
    "quotes, no trailing period."
)


def prompt(members: list[dict]) -> str:
    """The members as the model reads them: handle, title, plan, last report."""
    parts = []
    for m in members:
        lines = [f"- {m['handle']}: {m.get('title') or '(no title)'}"]
        if m.get("worker"):
            lines.append(f"  worker of queue {m['worker']}")
        plan = [i for i in m.get("plan") or [] if i.get("state") != "done"]
        lines += [f"  plan [{i['state']}] {i['text']}" for i in plan[:PLAN_ITEMS]]
        if m.get("report"):
            lines.append(f"  last report: {m['report']}")
        parts.append("\n".join(lines))
    return "Members of the group:\n" + "\n".join(parts)


def clean(value: GroupNameOut) -> tuple[str | None, str]:
    """The name when it is a valid handle-shaped name (else None, keeping the
    old one), and the title cut to one line of ``TITLE_MAX``."""
    name = value.name.strip().lower()
    title = " ".join(value.title.split())[:TITLE_MAX].rstrip(" .")
    return (name if NAME_RE.match(name) else None), title


def _member(s) -> dict:
    report = s.standing.get("report") or {}
    return {
        "handle": s.handle,
        "title": s.title,
        "worker": (s.worker or {}).get("queue"),
        "plan": s.standing.get("plan") or [],
        "report": report.get("line"),
    }


class GroupNames:
    """One pending or running call per group; the last membership it named."""

    def __init__(self, app) -> None:
        self.app = app
        self._tasks: dict[str, asyncio.Task] = {}
        self._named: dict[str, frozenset[str]] = {}

    def changed(self, key: str) -> None:
        """A member joined or left ``key``: name it again once things settle."""
        if oneshot.load_generator(self.app.roots.config_root, "group_names") is None:
            return
        if (t := self._tasks.get(key)) is not None:
            t.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no loop: a registry used outside the server
            return
        task = loop.create_task(self._after(key))
        self._tasks[key] = task
        task.add_done_callback(
            lambda t, k=key: (
                self._tasks.pop(k, None) if self._tasks.get(k) is t else None
            )
        )

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    async def _after(self, key: str) -> None:
        await asyncio.sleep(DEBOUNCE_S)
        try:
            await self._make(key)
        except Exception:
            log.exception("aegis: naming group %s failed", key)

    async def _make(self, key: str) -> None:
        reg = self.app.sessions
        if reg.groups.get(key) is None:
            self._named.pop(key, None)
            return
        members = [s for s in reg.sessions.values() if s.spec.group == key]
        ids = frozenset(s.log_id for s in members)
        if not members or self._named.get(key) == ids:
            return
        cfg = oneshot.load_generator(self.app.roots.config_root, "group_names")
        if cfg is None or "error" in cfg:
            return
        try:
            out = await oneshot.run(
                oneshot.argv(
                    self.app._bin(cfg["harness"]),
                    cfg["model"],
                    GroupNameOut,
                    SYSTEM,
                    harness=cfg["harness"],
                ),
                self.app.roots.state_root / "oneshot",
                TIMEOUT_S,
                oneshot.message(
                    SYSTEM, prompt([_member(s) for s in members]), cfg["harness"]
                ),
                harness=cfg["harness"],
            )
        except (oneshot.OneshotError, PermissionError) as e:
            log.warning("aegis: naming group %s: %s", key, e)
            return
        value, _, _ = oneshot.parse(out, GroupNameOut)
        if value is None:
            return
        name, title = clean(value)
        if reg.retitle_group(key, name, title):
            self._named[key] = ids
