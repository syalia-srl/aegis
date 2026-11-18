"""aegis: a web-native workplace for coding agents.

Built from scratch next to the old ``aegis`` tree and never importing it. The
vision and each slice's design are under ``docs/superpowers/specs/``.

The names in ``__all__`` are the library's stable surface (help page
``library``); every other module may change in any release. They load on
first use, so ``import aegis`` stays as cheap as the CLI needs it to be.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .api.models import Turn
    from .api.queues import Task
    from .api.schedules import Schedule
    from .api.sessions import Session, SessionNotFound
    from .api.workflows import WorkflowRun
    from .ops import OpError
    from .policy import (
        Catalog,
        Credential,
        Event,
        HostPolicy,
        McpServer,
        Refused,
        SessionView,
        SpawnRequest,
        StandalonePolicy,
    )
    from .server import Server, StartError

__all__ = [
    "Catalog",
    "Credential",
    "Event",
    "HostPolicy",
    "McpServer",
    "Refused",
    "SessionView",
    "SpawnRequest",
    "StandalonePolicy",
    "OpError",
    "Schedule",
    "Server",
    "Session",
    "SessionNotFound",
    "StartError",
    "Task",
    "Turn",
    "WorkflowRun",
]

_WHERE = {
    "OpError": ".ops",
    "Catalog": ".policy",
    "Credential": ".policy",
    "Event": ".policy",
    "HostPolicy": ".policy",
    "McpServer": ".policy",
    "Refused": ".policy",
    "SessionView": ".policy",
    "SpawnRequest": ".policy",
    "StandalonePolicy": ".policy",
    "Server": ".server",
    "StartError": ".server",
    "Session": ".api.sessions",
    "SessionNotFound": ".api.sessions",
    "Turn": ".api.models",
    "Task": ".api.queues",
    "WorkflowRun": ".api.workflows",
    "Schedule": ".api.schedules",
}


def __getattr__(name: str) -> Any:
    if name in _WHERE:
        return getattr(import_module(_WHERE[name], __name__), name)
    raise AttributeError(f"module 'aegis' has no attribute {name!r}")
