"""aegis inside another program: ``async with Server(root) as srv``.

The Server builds what ``aegis serve`` builds (an App, then build_web) and runs
uvicorn as a task on a socket it bound first, so the port is known before the
App needs it in ``base_url``. Starlette's lifespan still calls ``boot()`` and
``shutdown()``, in the CLI's order. It always listens, on loopback by default:
harness children reach aegis's tools at ``<base_url>/mcp``, so a server without
a listener would spawn agents with no aegis tools. It installs no signal
handlers; the host program stops it by leaving the block.

``prefix`` is where the host mounts ``srv.asgi``. Only the URLs aegis generates
carry it, and the MCP routes stay on the loopback listener alone, so a host's
public prefix never serves ``/mcp``; the listener's page is not browsable under
a prefix, and the host serves it.

One Server runs per process at a time: ``launch.configure`` sets a mode every
child process reads, and two servers would share it (spec 2026-10-23).
"""

from __future__ import annotations

import asyncio
import copy
import contextlib
import socket
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .ops import Caller, Principal

if TYPE_CHECKING:
    from .api.ops import Ops
    from .api.queues import Queues
    from .api.schedules import Schedules
    from .api.sessions import Sessions
    from .api.workflows import Workflows
    from .policy import HostPolicy, McpServer
    from .app import App
    from .roots import Roots
    from starlette.applications import Starlette


class StartError(Exception):
    """A startup check failed. ``lines`` is what ``aegis serve`` prints."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__("\n".join(lines))
        self.lines = lines


@dataclass(frozen=True)
class Prepared:
    roots: Roots


def _proc_status() -> str:
    try:
        return Path("/proc/self/status").read_text()
    except OSError:
        return ""


def _ambient_caps(status: str) -> bool:
    """Whether this process holds ambient capabilities, which every child
    inherits: a systemd unit made for the multi-user mode grants them."""
    for line in status.splitlines():
        if line.startswith("CapAmb:"):
            return int(line.split(":", 1)[1].strip() or "0", 16) != 0
    return False


def prepare(start: Path, root: Path | None = None) -> Prepared:
    """The checks before a server may start on ``root`` (default: the nearest
    ancestor of ``start`` holding .aegis.yaml); StartError when one fails."""
    from .config import Config
    from .roots import legacy_state, make_roots

    roots = make_roots(start=start, root=root)
    if found := legacy_state(roots.state_root):
        raise StartError(
            [
                f"{roots.state_root} holds the state of aegis before 2.0 ({', '.join(found)}).",
                "Move it aside, then start again:",
                f"  mv {roots.state_root} {roots.state_root.parent / 'legacy-state'}",
            ]
        )
    from .tokens import OldStateError, refuse_old_state

    try:
        refuse_old_state(roots.state_root)
    except OldStateError as e:
        raise StartError([str(e)]) from None
    if _ambient_caps(_proc_status()):
        raise StartError(
            [
                "aegis holds ambient capabilities, which every agent would inherit: "
                "its systemd unit was made for the multi-user mode, which aegis 3.0 "
                "removed.",
                "Write a new unit with `aegis service install` and restart the service.",
            ]
        )
    snap = Config(roots.config_root).current()
    if snap.multi_user_error:
        raise StartError([snap.multi_user_error])
    return Prepared(roots)


def allowed_hosts(host: str, port: int) -> set[str]:
    """The Host headers a socket may name: loopback by every spelling, and ``host``."""
    return {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}", f"{host}:{port}"}


_running: Server | None = None


class Server:
    """An aegis server in this process. See the help page ``library``."""

    def __init__(
        self,
        root: str | Path,
        *,
        port: int = 0,
        host: str = "127.0.0.1",
        name: str | None = None,
        claude: str = "claude",
        opencode: str = "opencode",
        codex: str = "codex",
        cursor: str = "cursor-agent",
        origins: Iterable[str] = (),
        policy: HostPolicy | None = None,
        mcp: Mapping[str, McpServer] | None = None,
        prefix: str = "",
    ) -> None:
        # Required: nothing below the CLI reads the working directory (test_no_cwd).
        self._root = Path(root)
        self._port = port
        self._host = host
        self._name = name
        self._bins = (claude, opencode, codex, cursor)
        self._origins = list(origins)
        self._policy = policy
        self._mcp = mcp
        self._prefix = prefix
        self._app: App | None = None
        self._asgi: Starlette | None = None
        self._uvicorn: Any = None
        self._task: asyncio.Task | None = None
        self._caller: Caller | None = None
        self.token = ""
        self.base_url = ""

    # -- lifecycle ------------------------------------------------------------
    async def __aenter__(self) -> Server:
        global _running
        if _running is not None:
            raise RuntimeError(
                "an aegis Server is already running in this process; "
                "leave its block before starting another"
            )
        _running = self
        try:
            await self._start()
        except BaseException:
            _running = None
            raise
        return self

    async def __aexit__(self, *exc: object) -> None:
        global _running
        try:
            if self._uvicorn is not None:
                self._uvicorn.should_exit = True
            if self._task is not None:
                await self._task
        finally:
            _running = None

    async def _start(self) -> None:
        import uvicorn

        from .app import App
        from .web import build_web, load_or_create_token, ws_protocol

        p = prepare(self._root, self._root)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._host, self._port))
        except OSError as e:
            sock.close()
            raise StartError(
                [f"cannot listen on {self._host}:{self._port}: {e}"]
            ) from e
        port = sock.getsockname()[1]
        local = "127.0.0.1" if self._host in ("0.0.0.0", "localhost") else self._host
        self.base_url = f"http://{local}:{port}"
        self.token = load_or_create_token(p.roots.state_root)
        self._app = App(
            p.roots,
            base_url=self.base_url,
            server_name=self._name or socket.gethostname(),
            claude_bin=self._bins[0],
            opencode_bin=self._bins[1],
            codex_bin=self._bins[2],
            cursor_bin=self._bins[3],
            policy=self._policy,
            mcp=self._mcp,
        )
        web = build_web(
            self._app,
            self.token,
            allowed_hosts(self._host, port),
            self._origins,
            self._prefix,
        )
        # The listener keeps /mcp for harness children; the app a host mounts
        # on its public prefix does not (#993).
        self._asgi = build_web(
            self._app,
            self.token,
            allowed_hosts(self._host, port),
            self._origins,
            self._prefix,
            mcp=False,
        )
        self._caller = Caller(
            "user", principal=self._app.principals.resolve(self.token)
        )
        config = uvicorn.Config(
            web, log_level="warning", ws=ws_protocol(), lifespan="on"
        )
        self._uvicorn = _quiet_uvicorn(config)
        self._task = asyncio.create_task(self._uvicorn.serve(sockets=[sock]))
        while not self._uvicorn.started:
            if self._task.done():
                break
            await asyncio.sleep(0.02)
        if not self._uvicorn.started:
            # uvicorn logs a failed lifespan and returns; its error is in the log.
            err = self._task.exception() if self._task.done() else None
            sock.close()
            raise StartError([f"aegis did not start: {err or 'see the log'}"])

    # -- what a program uses --------------------------------------------------
    @property
    def url(self) -> str:
        """The web client's sign-in URL."""
        return f"{self.base_url}/?token={self.token}"

    @property
    def app(self) -> App:
        """The internal App, for what the API does not cover yet. Not stable."""
        if self._app is None:
            raise RuntimeError("the Server is not running: use it in `async with`")
        return self._app

    @property
    def asgi(self) -> Starlette:
        """The web app a host mounts at its own prefix, or a proxy reaches
        with the prefix stripped: ``app.mount("/aegis", srv.asgi)``. Its
        routes are relative; only the URLs it generates carry ``prefix``."""
        if self._asgi is None:
            raise RuntimeError("the Server is not running: use it in `async with`")
        return self._asgi

    @property
    def sessions(self) -> Sessions:
        from .api.sessions import Sessions

        return Sessions(self)

    @property
    def queues(self) -> Queues:
        from .api.queues import Queues

        return Queues(self)

    @property
    def workflows(self) -> Workflows:
        from .api.workflows import Workflows

        return Workflows(self)

    @property
    def schedules(self) -> Schedules:
        from .api.schedules import Schedules

        return Schedules(self)

    @property
    def ops(self) -> Ops:
        from .api.ops import Ops

        return Ops(self)

    def as_who(self, who: Any) -> Server:
        """This server's API acting as ``who``, one of the host policy's
        callers: ``srv.as_who(who).sessions.spawn()`` starts a session that
        is theirs, and lists show what the policy lets them see. ``srv``
        itself acts as the server's own caller."""
        if self._caller is None:
            raise RuntimeError("the Server is not running: use it in `async with`")
        key = self.app._host_key(who)
        self.app.whos[key] = who
        acting = copy.copy(self)
        acting._caller = Caller("user", principal=Principal(key, who))
        return acting

    def token_for(self, who: Any, ttl: float = 3600) -> str:
        """A signed token for ``who``, valid ``ttl`` seconds and across a
        restart, that the host's page passes to the client."""
        return self.app.token_for(who, ttl)

    def sign_in(self, response: Any, token: str, request: Any = None) -> Any:
        """Set the session cookie on ``response`` for a token from
        :meth:`token_for`, as the standalone page's ``?token=`` sign-in does.

        A host that hands its page a token needs this too: a websocket hello
        carries the token, but an HTTP fetch of a sent file or a page's frame
        cannot, and a narrowing policy refuses it without the cookie. The
        cookie is HttpOnly and SameSite=strict; pass the ``request`` so a
        host behind https gets ``Secure`` (when omitted, any https origin the
        server was built with does). A minted token expires after its ``ttl``,
        so a host that mints a new one signs the browser in again with it."""
        from .web import cookie_name, sign_in_cookie

        return sign_in_cookie(
            response,
            request,
            token,
            cookie_name(self.app.roots.state_root),
            self._origins,
        )

    async def _call(self, name: str, params: dict | None = None) -> Any:
        """Run operation ``name`` as the server's admin person."""
        if self._caller is None:
            raise RuntimeError("the Server is not running: use it in `async with`")
        return await self.app.registry.call(name, params or {}, self._caller)


def _quiet_uvicorn(config: Any) -> Any:
    """A uvicorn.Server without its signal handlers: the host program owns them."""
    import uvicorn

    class Quiet(uvicorn.Server):
        @contextlib.contextmanager
        def capture_signals(self) -> Iterator[None]:
            yield

    return Quiet(config)
