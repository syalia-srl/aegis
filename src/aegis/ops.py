"""The operation registry: every action in aegis is one registered operation.

An operation has a name, a pydantic params model and an async handler that
receives the params and the caller. A websocket ``call`` is one projection of
the registry; the operations marked ``agent`` are another, as MCP tools named
after the operation with the dot as an underscore (``monitor.start`` is the tool
``monitor_start``). An agent whose session runs with ``read`` permission
calls only the operations marked ``read``: its own card, reading, and waiting
on other sessions. The rule is here, not in each harness, so it holds for every
harness and follows a live ``/permission`` change (#305). Plugins will add operations to it. Nothing reaches the client as
an action that is not an operation (DESIGN.md, "One registry, every caller").
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from .policy import CallerInfo, HostPolicy, SessionView


class OpError(Exception):
    def __init__(self, code: str, message: str, data: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        # What a client needs to act on the refusal (harness_untested's
        # version, path and command); sent beside the message, never in MCP.
        self.data = data


@dataclass(frozen=True)
class Principal:
    """Who a caller is: ``name`` is what aegis stores as an owner, the host
    policy's ``key(who)``, and ``who`` the host's own object for it, never
    read by aegis (policy.py). ``who`` is None for the server's own caller."""

    name: str
    who: Any = field(default=None, compare=False)


@dataclass(frozen=True)
class Caller:
    """Who is calling: a person through the client, or an agent through MCP.
    An agent is known by its session's log id, resolved from its spawn's
    token, never from an argument (DESIGN.md, "Caller identity is a
    transport fact")."""

    kind: str  # "user" | "agent"
    log_id: str | None = None
    # A person whose browser runs on the server's own desktop, so an action
    # like opening a file in a desktop app reaches them. Set by the transport.
    desktop: bool = False
    # A person acting through a link from another aegis server: that server's
    # name. Set by the transport from the link's hello (links.py).
    link: str | None = None
    # Who the caller's token is (tokens.py). None for an agent, whose
    # principal is its session's owner, and for the server's own calls.
    principal: Principal | None = None

    @property
    def is_agent(self) -> bool:
        return self.kind == "agent"


USER = Caller("user")

# What a named id is, and the code an operation answers when the caller may
# not read its session: the code it answers for one that does not exist.
KINDS = {
    "session": "no_session",
    "task": "no_task",
    "run": "no_run",
    "browser": "unknown_browser",
    "monitor": "no_monitor",
}

# What a schedule's run with spawn: false may not call: each starts other work
# (schedules.py). schedule.create is here so a run cannot start agents through
# a schedule of its own.
NO_SPAWN = ("session.spawn", "session.handoff", "queue.enqueue", "schedule.create")

Handler = Callable[[Any, Caller], Awaitable[Any]]


@dataclass(frozen=True)
class Operation:
    name: str
    params: type[BaseModel]
    handler: Handler
    agent: bool = False
    doc: str = ""
    # Open to an agent whose session runs with read permission.
    read: bool = False
    # Refused to a caller acting through a link: a far server never reads or
    # writes this server's files (fs_ops.py).
    local: bool = False
    # The params that name something a session owns, as (field, kind) with
    # kind one of KINDS, and whether the operation reads or writes it: the
    # host policy's can decides (Registry.call, #959).
    names: tuple[tuple[str, str], ...] = ()
    access: str = "write"
    # Acts on the whole server (its config, links, files, shells, harness
    # sign-ins): HostPolicy.guard refuses it to every caller a host
    # identified; StandalonePolicy's one person keeps it (#959).
    server: bool = False

    @property
    def tool_name(self) -> str:
        return self.name.replace(".", "_")


class NoParams(BaseModel):
    model_config = {"extra": "forbid"}


def default_guard(op: Operation, caller: CallerInfo) -> None:
    """The checks that are not about people (HostPolicy.guard's default):
    operations closed to agents, a read-permission session, a schedule run
    with spawn: false, and operations closed to linked servers."""
    agent = caller.kind == "agent"
    if agent and not op.agent:
        raise OpError("not_for_agents", f"{op.name} is not open to agents")
    if agent and caller.tools is not None and op.tool_name not in caller.tools:
        raise OpError("not_for_agents", f"{op.tool_name} is not open to this session")
    if agent and not op.read and caller.permission == "read":
        raise OpError(
            "read_only",
            f"{op.tool_name} is not open to a session with read permission; "
            "it can call " + ", ".join(caller.read_tools),
        )
    if agent and op.name in NO_SPAWN:
        run = caller.schedule
        if run is not None and not run.get("spawn"):
            raise OpError(
                "spawn_off",
                f"run {run['run']} of {run['name']} may not start other work "
                "(spawn: false)",
            )
    if op.local and caller.link:
        raise OpError("not_across_links", f"{op.name} is not open to a linked server")


async def _nothing(caller: Caller) -> None:
    return None


class Registry:
    def __init__(self) -> None:
        self._ops: dict[str, Operation] = {}
        self._read: tuple[str, ...] | None = None  # read_tools, cached
        # A session's current permission by log id; set by the app.
        self.permission_of: Callable[[str], str | None] = lambda log_id: None
        # The principal a caller acts as, and a session's owner by log id; both
        # set by the app. Without an app every caller is an admin.
        self.who: Callable[[Caller], Principal] = lambda c: c.principal or Principal("")
        self.owner_of: Callable[[str], str | None] = lambda log_id: None
        # A session's schedule meta by log id (session.py); set by the app.
        self.schedule_of: Callable[[str], dict | None] = lambda log_id: None
        # Makes sure the who behind a caller is known before the call runs
        # (App.ensure_who); set by the app.
        self.ensure_who: Callable[[Caller], Awaitable[None]] = _nothing
        # The host policy (policy.py); set by the app. Without one, the
        # default guard runs and nothing wraps a handler.
        self.policy: HostPolicy | None = None
        # Whether the policy hides anything (App.narrows); set by the app.
        self.narrows: Callable[[], bool] = lambda: False
        # The session a named id belongs to, None when it names none here, and
        # a session as the policy sees it; both set by the app.
        self.session_of: Callable[[str, str], str | None] = lambda kind, value: None
        self.view_of: Callable[[str], SessionView | None] = lambda log_id: None
        # The aegis tools the policy gave a session's process; None is all.
        self.tools_of: Callable[[str], frozenset[str] | None] = lambda log_id: None

    def op(
        self,
        name: str,
        params: type[BaseModel] = NoParams,
        *,
        agent: bool = False,
        doc: str = "",
        read: bool = False,
        local: bool = False,
        names: tuple[tuple[str, str], ...] = (),
        access: str = "write",
        server: bool = False,
    ) -> Callable[[Handler], Handler]:
        def register(handler: Handler) -> Handler:
            if name in self._ops:
                raise ValueError(f"operation {name!r} registered twice")
            self._read = None
            self._ops[name] = Operation(
                name,
                params,
                handler,
                agent,
                doc or (handler.__doc__ or "").strip(),
                read,
                local,
                names,
                access,
                server,
            )
            return handler

        return register

    def names(self) -> list[str]:
        return sorted(self._ops)

    def _check_named(
        self, op: Operation, params: Any, who: Any, caller: Caller
    ) -> None:
        """Refuse a call naming a session ``who`` may not read as if it did not
        exist, and one it may read but not change as refused. An agent's own
        session counts as named."""
        assert self.policy is not None
        named = [
            (kind, value, self.session_of(kind, value))
            for field_, kind in op.names
            if isinstance(value := getattr(params, field_, None), str) and value
        ]
        if caller.is_agent and caller.log_id:
            named.append(("session", caller.log_id, caller.log_id))
        for kind, value, log_id in named:
            view = self.view_of(log_id) if log_id else None
            if view is None:
                continue  # not here: the handler answers for it
            if not self.policy.can(who, "read", view):
                raise OpError(KINDS[kind], f"no {kind} {value!r}")
            if op.access == "write" and not self.policy.can(who, "write", view):
                from .policy import Refused

                raise Refused(f"you may read {value} but not change it")

    def caller_info(self, caller: Caller) -> CallerInfo:
        from .policy import CallerInfo

        log_id = caller.log_id if caller.is_agent else None
        return CallerInfo(
            kind=caller.kind,
            log_id=caller.log_id,
            link=caller.link,
            permission=self.permission_of(log_id) if log_id else None,
            schedule=self.schedule_of(log_id) if log_id else None,
            read_tools=self._read_tools(),
            tools=self.tools_of(log_id) if log_id else None,
        )

    def _read_tools(self) -> tuple[str, ...]:
        if self._read is None:
            self._read = tuple(o.tool_name for o in self.agent_ops() if o.read)
        return self._read

    def agent_ops(self) -> list[Operation]:
        return [op for _, op in sorted(self._ops.items()) if op.agent]

    async def call(self, name: str, raw: object, caller: Caller = USER) -> Any:
        op = self._ops.get(name)
        if op is None:
            raise OpError("unknown_op", f"no operation named {name!r}")
        await self.ensure_who(caller)
        info = self.caller_info(caller)
        who = self.who(caller).who
        if self.policy is not None:
            self.policy.guard(op, who, info)
        else:
            default_guard(op, info)
        try:
            params = op.params.model_validate(raw if raw is not None else {})
        except ValidationError as e:
            raise OpError("bad_params", str(e)) from e
        if self.policy is not None and self.narrows():
            self._check_named(op, params, who, caller)
        try:
            if self.policy is None:
                return await op.handler(params, caller)
            return await self.policy.around(
                name, who, params, lambda p: op.handler(p, caller)
            )
        except PermissionError as e:
            # A refused file or process action: say so rather than as an
            # internal error.
            raise OpError("forbidden", str(e)) from e
