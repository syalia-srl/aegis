"""What one subscriber sees under the host policy (#959): channels.View.

The sessions and workflow-runs channels lose the rows of sessions the
subscriber may not list, and a row that turns hidden reaches it as a removal;
a transcript, a browser's or a run's own channel answers as missing when the
subscriber may not read its session. Every other channel passes as it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .app import App
    from .policy import Action
    from .ops import Principal

# The channels whose rows belong to a session, by the field that keys a row
# and the field that names its session.
ROWS = {"sessions": ("log_id", "log_id"), "workflow-runs": ("run", "owner")}


class PolicyView:
    def __init__(self, app: App, principal: Principal) -> None:
        self._app = app
        self._principal = principal
        self._who = principal.who
        self._sent: dict[str, set[Any]] = {}

    def _can(self, action: Action, log_id: str | None) -> bool:
        if not log_id:
            return True
        app = self._app
        view = app.view_of(log_id)
        if view is None:
            owner = app.session_of("browser", log_id)
            view = app.view_of(owner) if owner else None
        return view is None or app.policy.can(self._who, action, view)

    def allows(self, channel: str) -> bool:
        kind, _, ident = channel.partition(":")
        if kind == "transcript":
            return self._can("read", ident)
        if kind == "browser":
            return self._can("read", self._app.session_of("browser", ident))
        if kind == "workflow":
            return self._can("read", self._app.session_of("run", ident))
        return True

    def snapshot(self, channel: str, data: Any) -> Any:
        if channel == "schedules" and isinstance(data, list):
            return [r for r in data if self._owns(r)]
        if channel not in ROWS or not isinstance(data, list):
            return data
        key, owner = ROWS[channel]
        rows = [r for r in data if self._can("list", r.get(owner))]
        self._sent[channel] = {r.get(key) for r in rows}
        return rows

    def _owns(self, row: dict) -> bool:
        return self._app.owns_schedule(self._principal, str(row.get("name")))

    def op(self, channel: str, op: dict) -> dict | None:
        if channel == "schedules":
            row = op.get("upsert")
            return op if row is None or self._owns(row) else None
        if channel == "toasts":
            source = (op.get("toast") or {}).get("from") or {}
            return op if self._can("list", source.get("id")) else None
        if channel not in ROWS:
            return op
        key, owner = ROWS[channel]
        sent = self._sent.setdefault(channel, set())
        row = op.get("upsert")
        if row is None:
            k = op.get("remove")
            if k in sent:
                sent.discard(k)
                return op
            return None
        if self._can("list", row.get(owner)):
            sent.add(row.get(key))
            return op
        if row.get(key) in sent:
            sent.discard(row.get(key))
            return {"remove": row.get(key)}
        return None
