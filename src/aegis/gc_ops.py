"""Repository gc proposals are agent tools; approval belongs to the person.

The card's immutable inventory lives in the session transcript. Browser choices
are paths from that inventory, never a new command or an agent-supplied plan.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from . import repo_gc
from .monitors import iso_now
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App


class Reference(BaseModel):
    model_config = {"extra": "forbid"}
    log_id: str | None = None


class Propose(Reference):
    path: str | None = Field(
        None,
        description="Repository or directory of repositories under the config root. Omitted: the config root (its repos/ when present).",
    )
    days: int = Field(
        30, ge=1, description="Minimum days since both source and artifact activity."
    )


class Read(Reference):
    id: str


class Decide(Read):
    approve: bool
    paths: list[str] = Field(default_factory=list)


def register_gc_ops(app: App) -> None:
    r, reg = app.registry, app.sessions

    def session(p: Reference, caller: Caller):
        if caller.is_agent and p.log_id not in (None, caller.log_id):
            raise OpError(
                "not_for_agents", "an agent proposes cleanup only in its own session"
            )
        log_id = caller.log_id if caller.is_agent else p.log_id
        if not log_id:
            raise OpError("bad_params", "a user call needs log_id")
        return reg.open(log_id)

    def active() -> tuple[Path, ...]:
        return tuple(
            s.spec.cwd.resolve()
            for s in reg.sessions.values()
            if not s.archived and s.pid
        )

    @r.op(
        "gc.propose",
        Propose,
        agent=True,
        read=True,
        local=True,
        server=True,
        names=(("log_id", "session"),),
    )
    async def propose(p: Propose, caller):
        """Inspect rebuildable files in inactive repositories and show a
        selection card in your transcript. This deletes nothing. Only the
        person's click can approve the displayed selection; you cannot
        approve it through MCP. Return includes the inventory and exclusions.
        End your turn; approval or cancellation wakes you with > from gc:<id>.
        """
        s = session(p, caller)
        root = app.roots.config_root.resolve()
        path = (root / p.path).resolve() if p.path else root
        if not path.is_relative_to(root) or not path.is_dir():
            raise OpError(
                "bad_params", "path must be a directory under the config root"
            )
        try:
            inventory = await asyncio.to_thread(
                repo_gc.scan, path, days=p.days, active=active()
            )
        except (OSError, ValueError) as exc:
            raise OpError("inspection_failed", str(exc)) from exc
        id = "gc-" + secrets.token_hex(4)
        card = {
            "kind": "gc",
            "card_id": id,
            "status": "pending",
            "inventory": inventory,
        }
        s.record_card(card)
        return {"id": id, "status": "pending", "inventory": inventory}

    @r.op(
        "gc.read",
        Read,
        agent=True,
        read=True,
        local=True,
        server=True,
        names=(("log_id", "session"),),
        access="read",
    )
    async def read(p: Read, caller):
        """Read your cleanup proposal, its inventory and the person's outcome.
        Does not change the selection or approve deletion."""
        s = session(p, caller)
        e = s.fold().entry(p.id)
        if not e or e["kind"] != "gc":
            raise OpError("not_found", f"no gc proposal {p.id}")
        return e["detail"]

    @r.op("gc.decide", Decide, local=True, server=True, names=(("log_id", "session"),))
    async def decide(p: Decide, caller):
        if caller.is_agent:
            raise OpError(
                "not_for_agents", "only a person can approve repository cleanup"
            )
        s = session(p, caller)
        e = s.fold().entry(p.id)
        if not e or e["kind"] != "gc":
            raise OpError("not_found", f"no gc proposal {p.id}")
        if e["status"] != "pending":
            raise OpError("gone", "this cleanup proposal has already been answered")
        card: dict = {"kind": "gc", **e["detail"]}
        inventory = card["inventory"]
        eligible = {x["path"] for x in inventory["rows"] if not x.get("reason")}
        if p.approve and (
            not p.paths or len(p.paths) != len(set(p.paths)) or set(p.paths) - eligible
        ):
            raise OpError(
                "bad_params", "select eligible paths from the displayed proposal"
            )
        # Mark before the first await: two browser clicks cannot execute twice.
        card.update(
            status="running" if p.approve else "cancelled",
            selected=p.paths if p.approve else [],
        )
        s.record_card(card)
        if p.approve:
            try:
                result = await asyncio.to_thread(
                    repo_gc.execute, inventory, p.paths, active=active()
                )
            except (OSError, ValueError) as exc:
                result = {"removed": [], "errors": [{"reason": str(exc)}]}
            card.update(status="finished", result=result)
            s.record_card(card)
        await s.deliver(
            f"> from gc:{p.id} · {card['status']} · {iso_now()}",
            json.dumps(card.get("result", {"cancelled": True}), ensure_ascii=False),
        )
        return card
