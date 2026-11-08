"""A host program for the mount's browser tests (plan 2026-11-08, slice a2).

It runs an aegis ``Server`` with a host policy under ``/aegis``, one session
owned by Ana, and a Starlette page that mounts the client at that prefix. The
server is the same one a Python host embeds: ``app.mount("/aegis", srv.asgi)``
with the loopback listener still serving the MCP tools at its own root.

The page is a stranger's: a board of its own, and a #chat div that the client
mounts into. It records every socket and message on ``window`` so the browser
test can count sockets and read the welcome without reaching into aegis's
objects; ``window.__chat`` is the handle mount() returned.

Run it as ``python -m tests.host_fixture --root <dir> --port <port>
--claude <binary>``; it prints one JSON line when the page is ready
(``{"url": ..., "session": ...}``) and serves until stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path

from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

from aegis import Server, SessionNotFound

try:  # as a package, under pytest
    from .policies import Named, Who
except ImportError:  # as `python tests/host_fixture.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from tests.policies import Named, Who

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: low, permission: full}
"""

HOST_PAGE = """\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>aegis host</title>
</head>
<body>
<main id="board">A host page</main>
<div id="chat"></div>
<div id="chat2"></div>
<script>
window.aegisHost = {token: "__TOKEN__", session: "__SESSION__", session2: "__SESSION2__", prefix: "__PREFIX__"};
// What the test reads: every socket the page opens and every message it hears.
// Wrapping WebSocket here runs before the module script below, which is
// deferred until parsing ends.
window.__ws = [];
window.__wsMsgs = [];
const Native = window.WebSocket;
window.WebSocket = class extends Native {
  constructor(...args) {
    super(...args);
    window.__ws.push(this);
    this.closed = false;
    this.addEventListener("message", (ev) => {
      try { window.__wsMsgs.push(JSON.parse(ev.data)); } catch (e) {}
    });
    this.addEventListener("close", () => { this.closed = true; });
  }
};
window.__board = document.getElementById("board").outerHTML;
</script>
<script type="module">
import { mount } from "__PREFIX__/aegis.js";
const chat = mount(document.getElementById("chat"), {
  token: window.aegisHost.token,
  session: window.aegisHost.session,
  layout: "chat",
  theme: "host",
});
window.__chat = chat;
</script>
</body>
</html>
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def ensure_config(root: Path) -> None:
    """A minimal project so a bare directory runs the fixture."""
    path = root / ".aegis.yaml"
    if not path.exists():
        path.write_text(CONFIG)


async def read_or_spawn(srv: Server, who: Who, path: Path) -> str:
    """The one session Ana owns: the id in ``path`` if it is still open, or a
    new one, written there for the next run."""
    api = srv.as_who(who)
    if path.exists():
        try:
            return (await api.sessions.get(path.read_text().strip())).log_id
        except SessionNotFound:
            pass
    session = await api.sessions.spawn()
    path.write_text(session.log_id)
    return session.log_id


def host_app(srv: Server, page: str, prefix: str = "/aegis") -> Starlette:
    """The page at ``/`` and aegis mounted at ``prefix``: what a host serves."""

    async def index(_request):
        return HTMLResponse(page)

    return Starlette(
        routes=[Route("/", index), Mount(prefix, srv.asgi)],
    )


async def main(
    root: Path,
    port: int,
    *,
    claude: str = "claude",
    prefix: str = "/aegis",
    session_file: Path | None = None,
) -> None:
    import uvicorn

    ensure_config(root)
    origin = f"http://127.0.0.1:{port}"
    async with Server(
        root,
        claude=claude,
        policy=Named(),
        prefix=prefix,
        origins=[origin],
    ) as srv:
        who = Who("ana")
        first = session_file or root / "host-session"
        second = first.parent / f"{first.name}-2"
        sid = await read_or_spawn(srv, who, first)
        sid2 = await read_or_spawn(srv, who, second)
        page = (
            HOST_PAGE.replace("__TOKEN__", srv.token_for(who))
            .replace("__SESSION2__", sid2)
            .replace("__SESSION__", sid)
            .replace("__PREFIX__", prefix)
        )
        print(
            json.dumps({"url": f"{origin}/", "session": sid, "session2": sid2}),
            flush=True,
        )
        await uvicorn.Server(
            uvicorn.Config(
                host_app(srv, page, prefix),
                host="127.0.0.1",
                port=port,
                log_level="warning",
            )
        ).serve()


def cli() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--claude", default="claude")
    parser.add_argument("--prefix", default="/aegis")
    parser.add_argument("--session-file", type=Path)
    args = parser.parse_args()
    asyncio.run(
        main(
            args.root,
            args.port or free_port(),
            claude=args.claude,
            prefix=args.prefix,
            session_file=args.session_file,
        )
    )


if __name__ == "__main__":
    cli()
