"""A host program for the mount's browser tests (plan 2026-11-08, slice a2).

It runs an aegis ``Server`` with a host policy under ``/aegis``, one session
owned by Ana, and a Starlette page that mounts the client at that prefix. The
server is the same one a Python host embeds: ``app.mount("/aegis", srv.asgi)``
with the loopback listener still serving the MCP tools at its own root.

The page is a stranger's: a board, tabs and a composer of its own, a host
stylesheet that sets every variable aegis reads, and a #chat div that the
client mounts into, beside an empty #chat2 and #tabsbox for the cases that
mount a second chat or the tabs layout. It records every socket and message on ``window`` so the
browser test can count sockets and read the welcome without reaching into
aegis's objects; ``window.__chat`` is the handle mount() returned, and
``window.__host`` is the page's own boxes as they stood before the mount drew.

The host's stylesheet hides tool calls only when the page is loaded with
``?tools=hidden``: the tests that click a tool row need the row on screen, and
a host turning them off is a rule of the page, not of the mount (slice a8).

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
<style>
  /* The host's own page: its colours and fonts, the ids it already uses (tabs,
     composer), and one rule that hides tool calls. aegis reads these variables
     (theme: "host", spec 06 Styling) and must not reach anything else. The
     block sits in the body, after the client's stylesheet in the head, so a
     rule of the host's that ties with one of aegis's still wins. */
  :root { --bg:#fbf8f2; --chrome:#f2ece1; --side:#f6f1e7; --surface:#fff;
          --raised:#fdfbf6; --rule:#e2dccf; --ink:#2b2722; --strong:#17140f;
          --muted:#6b655c; --faint:#8d867b; --accent:#b0482f;
          --accent-soft:#f3e4de; --on-accent:#fff; --ok:#4f7a3a; --err:#b23b2e;
          --err-bg:#fbeceb; --warn:#b07a2f; --comms:#466a8f; --add-bg:#eef3e6;
          --del-bg:#fbeceb; --h-kw:#8a5a2b; --h-str:#4f7a3a; --h-num:#b0482f;
          --h-com:#8d867b; --h-fn:#2b2722; --h-type:#466a8f; --h-var:#6b655c;
          --h-meta:#8d867b; --font-ui:Georgia,serif; --font-prose:Georgia,serif;
          --font-mono:ui-monospace; --font-chrome:ui-monospace;
          --font-head:Georgia,serif; --prose-size:16px; --r:4px; --r-lg:8px;
          --time:none; --thread:block; --row-cols:22px 1fr; --fill:var(--accent); }
  html { overflow-y: scroll; }  /* a constant gutter: the mount's height is not this page's */
  body { margin: 0; background: var(--surface); color: var(--ink);
         font-family: var(--font-ui); }
  #board { padding: 18px 16px; font-size: 24px; }
  /* The host's own boxes are styled by class: an id aegis also uses (#composer,
     #input) would match a node inside the mount too, and the host wants its
     own box, not aegis's. */
  .host-tabs { padding: 10px 16px; border-bottom: 1px solid var(--rule);
               font-family: var(--font-chrome); font-size: 12px; }
  .host-input { margin: 0 16px 12px; padding: 8px 12px; width: 320px;
                border: 1px solid var(--rule); border-radius: var(--r);
                font-size: 13px; }
  #chat { margin: 0 16px 16px; border: 1px solid var(--rule); }
  /* The height is a host's default; :where keeps it at zero specificity so a
     test's own #chat rule (or a host's) still decides. */
  :where(#chat) { height: 560px; }
  [data-hide-tools] .aegis .tool { display: none; }
</style>
<div id="tabs" class="host-tabs">the host's own tabs</div>
<main id="board">A host page</main>
<div id="composer" class="host-input">the host's own input</div>
<div id="chat"></div>
<div id="chat2"></div>
<div id="tabsbox"></div>
<script>
window.aegisHost = {token: "__TOKEN__", session: "__SESSION__", session2: "__SESSION2__", prefix: "__PREFIX__"};
// A host that wants no tool calls turns them off with its own CSS. It is asked
// for with ?tools=hidden because the tests that click a tool row need the row
// on screen, and the rule is the page's, never the mount's (slice a8).
if (new URLSearchParams(location.search).get("tools") === "hidden") {
  document.documentElement.dataset.hideTools = "";
}
// The host's own boxes as they stood once the page's stylesheet applied and
// before the mount drew anything: the test compares text, size and position
// after it, and no aegis rule may move them.
window.__host = (() => {
  const out = {};
  for (const id of ["board", "tabs", "composer"]) {
    const n = document.getElementById(id);
    const r = n.getBoundingClientRect();
    out[id] = {text: n.textContent, x: r.x, y: r.y, width: r.width, height: r.height};
  }
  return out;
})();
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
  // ?lang=es mounts the chat in Spanish (slice b9); English otherwise.
  lang: new URLSearchParams(location.search).get("lang") || "en",
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


def host_app(
    srv: Server,
    page: str,
    prefix: str = "/aegis",
    token: str = "",
) -> Starlette:
    """The page at ``/`` and aegis mounted at ``prefix``: what a host serves.

    ``token`` signs this browser in the way a sent file's HTTP fetch needs:
    the same signed token the page gives mount(), set as the server's session
    cookie with ``srv.sign_in``. A WebSocket hello carries the token itself,
    but an ``<img>`` or a preview's fetch cannot.
    """

    async def index(request):
        response = HTMLResponse(page)
        return srv.sign_in(response, token, request) if token else response

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
        token = srv.token_for(who)
        page = (
            HOST_PAGE.replace("__TOKEN__", token)
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
                host_app(
                    srv,
                    page,
                    prefix,
                    token,
                ),
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
