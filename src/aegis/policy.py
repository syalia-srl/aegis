"""The host policy: what a program that embeds aegis decides (#959).

aegis asks one ``HostPolicy`` at every decision about a caller: who a token
is, what is stored as an owner, whether an operation may run, which sessions a
caller sees, what a spawn runs, which aegis tools a harness gets, and what
happened. aegis passes the host's ``who`` around without reading it, and
stores only ``key(who)``. ``aegis serve`` runs ``StandalonePolicy``: one
caller, the holder of the server's token, who may do everything. Help page
``library``, "Host policy".
"""

from __future__ import annotations

import dataclasses
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from .ops import OpError, default_guard

if TYPE_CHECKING:
    from pydantic import BaseModel

    from .app import App
    from .ops import Operation

Action = Literal["list", "read", "write"]
EventKind = Literal["start", "turn_end", "entry", "stop", "close", "file"]


class Refused(OpError):
    """A host's refusal: the caller reads ``message`` as why."""

    def __init__(self, message: str) -> None:
        super().__init__("refused", message)


@dataclass(frozen=True)
class Credential:
    """A raw token and where it came from: a browser's websocket, an HTTP
    request, or a linked server's hello (``server`` names it)."""

    token: str
    via: Literal["browser", "http", "link"]
    server: str | None = None


@dataclass(frozen=True)
class CallerInfo:
    """What ``guard`` knows about a call beyond its operation and its who."""

    kind: str  # "user" | "agent"
    log_id: str | None
    link: str | None
    # The calling agent's session's permission and schedule run meta.
    permission: str | None
    schedule: dict | None
    # The tool names open to a session with read permission, for the refusal.
    read_tools: tuple[str, ...]
    # The aegis tools ``tools`` gave the calling agent's session; None is all.
    tools: frozenset[str] | None = None


@dataclass(frozen=True)
class SessionView:
    """A session as the hooks see it. ``owner`` is the stored key, so
    ``restore(view.owner)`` gives its who."""

    log_id: str
    owner: str
    agent: str
    harness: str
    model: str
    cwd: str
    spawned_from: str | None
    archived: bool
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SpawnRequest:
    """What a start runs. ``spawn`` returns it, possibly rewritten with
    ``replace``. ``system_prompt`` is added to the agent's own. The last four
    make a product agent (#983): ``harness_tools`` False leaves the harness
    none of its own tools, ``isolated`` runs it without the machine's config
    in an empty folder, ``primer`` False drops aegis's workplace prompt, and
    ``notices`` False keeps aegis's nudges and notices out of the session."""

    agent: str
    harness: str
    model: str
    effort: str
    permission: str
    cwd: Path
    env: dict[str, str]
    system_prompt: str | None
    tags: dict[str, str]
    harness_tools: bool = True
    isolated: bool = False
    primer: bool = True
    notices: bool = True

    def replace(self, **changes: Any) -> SpawnRequest:
        return dataclasses.replace(self, **changes)


@dataclass(frozen=True)
class Catalog:
    """What a caller's pickers offer. None in a field is everything aegis has."""

    agents: tuple[str, ...] | None = None
    harnesses: tuple[str, ...] | None = None
    models: tuple[str, ...] | None = None
    efforts: tuple[str, ...] | None = None
    permissions: tuple[str, ...] | None = None
    folders: tuple[str, ...] | None = None


@dataclass(frozen=True)
class Event:
    """Something that happened, for ``on_event``."""

    kind: EventKind
    log_id: str
    data: dict = field(default_factory=dict)
    # The stored key of the session's owner (restore(owner) gives its who);
    # None when the session is unknown here.
    owner: str | None = None


@dataclass(frozen=True)
class McpServer:
    """An MCP server a host adds to a session (``mcp``), over HTTP or stdio."""

    name: str
    transport: Literal["http", "stdio"]
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    command: str | None = None
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def http(
        cls, name: str, url: str, headers: dict[str, str] | None = None
    ) -> McpServer:
        return cls(name, "http", url=url, headers=dict(headers or {}))

    @classmethod
    def stdio(
        cls,
        name: str,
        command: str,
        args: tuple[str, ...] | list[str] = (),
        env: dict[str, str] | None = None,
    ) -> McpServer:
        return cls(
            name, "stdio", command=command, args=tuple(args), env=dict(env or {})
        )


Call = Callable[["BaseModel"], Awaitable[Any]]


class HostPolicy:
    """Subclass and override what the host decides; each default allows.
    See the help page ``library``, "Host policy"."""

    OWNER = "owner"

    def attach(self, app: App) -> None:
        """Internal: the App this policy serves."""
        self._app = app

    async def identify(self, cred: Credential) -> Any:
        """The who a raw token is, or raise Refused. aegis asks only for a
        token it cannot read itself: the Server's own token is the Server's
        own caller (who None) and a ``token_for`` token is the who it was
        minted for, except over a link, which always comes here. The default
        refuses links and every other token."""
        if cred.via == "link":
            raise Refused("this server takes no calls from linked servers")
        raise Refused("unknown token")

    def key(self, who: Any) -> str:
        """What aegis stores as the owner of what ``who`` starts."""
        return self.OWNER

    async def restore(self, key: str) -> Any:
        """The who a stored key names, with nobody connected."""
        return key

    def guard(self, op: Operation, who: Any, caller: CallerInfo) -> None:
        """The checks that are not about people; raise OpError to refuse. The
        default keeps aegis's own: operations closed to agents, read-permission
        sessions, schedule runs with spawn: false, operations closed to links.
        Call ``super().guard(...)`` to keep them. It also refuses the
        operations that act on the whole server (``Operation.server``: config,
        links, files, shells, harness sign-ins, usage) to every caller you
        identified; the server's own caller (who None) keeps them."""
        default_guard(op, caller)
        if op.server and who is not None:
            raise Refused(f"{op.name} acts on the whole server")

    def can(self, who: Any, action: Action, session: SessionView) -> bool:
        return True

    def catalog(self, who: Any) -> Catalog:
        return Catalog()

    async def spawn(self, who: Any, req: SpawnRequest) -> SpawnRequest:
        return req

    def tools(self, who: Any, session: SessionView) -> set[str] | None:
        """Which of aegis's own tools the session's harness gets; None is all."""
        return None

    def mcp(self, who: Any, session: SessionView) -> list[McpServer]:
        return []

    async def around(self, op: str, who: Any, params: BaseModel, call: Call) -> Any:
        return await call(params)

    async def on_event(self, who: Any, event: Event) -> None:
        return None


class StandalonePolicy(HostPolicy):
    """``aegis serve``'s policy: the holder of the server's token may do
    everything, and linked servers are taken as before. Its key is the server
    owner's name, which every owner field on disk already holds."""

    def guard(self, op: Operation, who: Any, caller: CallerInfo) -> None:
        default_guard(op, caller)

    async def identify(self, cred: Credential) -> Any:
        if self._app.principals.resolve(cred.token) is None:
            raise Refused("unknown token")
        return self._app.principals.owner

    def key(self, who: Any) -> str:
        return self._app.principals.owner

    async def restore(self, key: str) -> Any:
        return self._app.principals.owner
