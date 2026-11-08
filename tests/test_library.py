"""aegis as a library: a Server in this process, driven through Session and
srv.ops, with the fake claude behind it (spec 2026-10-23-library-server)."""

import asyncio
import json
import signal
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from websockets.asyncio.client import connect

from aegis import OpError, Server, Session, SessionNotFound, StartError, Turn
from aegis.web import PROTO

from .policies import Named, Who
from aegis.api import (
    Prose,
    RunInfo,
    ScheduleInfo,
    ScheduleNotFound,
    SessionInfo,
    TaskInfo,
    TaskNotFound,
    ToolCall,
    TurnEnded,
    UserMessage,
)
from aegis.api.stubs import render

CONFIG = """\
default_agent: opus
max_workers: 1
agents:
  opus: {harness: claude-code, model: opus, effort: low, permission: full}
queues:
  work: {agent: opus}
"""

STUB = Path(__file__).parents[1] / "src" / "aegis" / "api" / "ops.pyi"


class NoSpawn(Named):
    """A host policy that refuses every start, whoever asks."""

    def can(self, who, action, session):
        if action == "spawn":
            return False
        return super().can(who, action, session)


@pytest.fixture
async def srv(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(tmp_path, claude=fake_claude, name="lab") as s:
        yield s


async def test_it_listens_on_a_free_port_and_serves_the_client(srv: Server):
    assert srv.base_url.startswith("http://127.0.0.1:")
    async with httpx.AsyncClient(follow_redirects=True) as c:
        r = await c.get(srv.url)
    assert r.status_code == 200


async def test_a_spawned_turn_ends_with_its_text(srv: Server):
    s = await srv.sessions.spawn("/md hello")
    turn = await s.wait(timeout=20)
    assert isinstance(turn, Turn)
    assert turn.text == "hello"
    assert turn.attention == "done"
    info = await s.info()
    assert isinstance(info, SessionInfo)
    assert (info.log_id, info.state, info.agent) == (s.log_id, "idle", "opus")


async def test_ask_returns_only_its_own_turn(srv: Server):
    s = await srv.sessions.spawn("/md first")
    await s.wait(timeout=20)
    turn = await s.ask("/md second", timeout=20)
    assert turn.text == "second"
    assert isinstance(turn.entries[0], UserMessage)
    assert turn.entries[0].md == "/md second"
    assert turn.cost_usd is not None and turn.cost_usd > 0


async def test_events_follow_a_tool_turn_in_order(srv: Server):
    # No prompt: the first message is the harness's first turn. Its card can
    # be read idle while the turn's rows are still queued behind it, and every
    # row must still come before TurnEnded (failed 24 of 24 under load).
    s = await srv.sessions.spawn()
    await s.wait(timeout=20)
    seen: list = []

    async def follow() -> None:
        async for ev in s.events():
            seen.append(ev)
            if isinstance(ev, TurnEnded):
                return

    follower = asyncio.create_task(follow())
    await asyncio.sleep(0.1)  # subscribed before the message goes out
    await s.send("/bash listing => a.txt")
    await asyncio.wait_for(follower, 20)
    kinds = [type(e) for e in seen if type(e) in (UserMessage, ToolCall, Prose)]
    assert kinds == [UserMessage, ToolCall]
    assert isinstance(seen[-1], TurnEnded)
    tool = next(e for e in seen if isinstance(e, ToolCall))
    assert tool.status == "ok"


async def test_interrupt_ends_a_long_turn(srv: Server):
    s = await srv.sessions.spawn("/busy 30")
    await asyncio.sleep(0.5)
    await s.interrupt()
    turn = await s.wait(timeout=10)
    assert turn.attention != "working"


async def test_get_list_and_close(srv: Server):
    s = await srv.sessions.spawn("/md hi")
    await s.wait(timeout=20)
    info = await s.info()
    assert await srv.sessions.get(info.handle) == s == await srv.sessions.get(s.log_id)
    assert s in await srv.sessions.list()
    await s.close()
    assert s not in await srv.sessions.list()
    with pytest.raises(SessionNotFound):
        await s.info()
    with pytest.raises(SessionNotFound):
        await srv.sessions.get("no-such-session")


async def test_every_operation_is_a_typed_method(srv: Server):
    assert await srv.ops.server.version()
    assert await srv.ops.meta()  # an operation with no dot
    with pytest.raises(OpError) as e:
        await srv.ops.session.spawn(bogus=1)
    assert e.value.code == "bad_params"
    with pytest.raises(AttributeError):
        srv.ops.session.no_such_op
    names = {f"{ns}.{m}" for ns in dir(srv.ops) for m in dir(getattr(srv.ops, ns))}
    dotted = {n for n in srv.app.registry.names() if "." in n}
    assert {n.replace(".", "_").replace("_", ".", 1) for n in dotted} <= names | dotted


async def test_the_committed_stub_matches_the_registry(srv: Server):
    fresh = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--stdin-filename", "ops.pyi", "-"],
        input=render(srv.app.registry),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert fresh == STUB.read_text(), "run `make ops-stubs` and commit ops.pyi"


async def test_a_policy_that_refuses_spawn_refuses_the_api_and_the_websocket(
    tmp_path: Path, fake_claude: str
):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(
        tmp_path, claude=fake_claude, name="lab", policy=NoSpawn()
    ) as srv:
        with pytest.raises(OpError) as e:
            await srv.sessions.spawn()
        assert e.value.code == "refused"
        token = srv.token_for(Who("ana"))
        async with connect(
            f"{srv.base_url.replace('http', 'ws')}/ws", origin=srv.base_url
        ) as ws:
            await ws.send(json.dumps({"t": "hello", "token": token, "proto": PROTO}))
            assert json.loads(await ws.recv())["t"] == "welcome"
            await ws.send(
                json.dumps({"t": "call", "id": 1, "op": "session.spawn", "params": {}})
            )
            reply = json.loads(await ws.recv())
            assert reply["t"] == "reply"
            assert reply["error"]["code"] == "refused"


async def test_one_server_per_process(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    async with Server(tmp_path, claude=fake_claude):
        with pytest.raises(RuntimeError):
            async with Server(tmp_path, claude=fake_claude):
                pass
    async with Server(tmp_path, claude=fake_claude) as again:
        assert again.token


async def test_a_failed_check_raises_instead_of_exiting(tmp_path: Path):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "daemon.lock").write_text("")
    with pytest.raises(StartError) as e:
        async with Server(tmp_path):
            pass
    assert "before 2.0" in e.value.lines[0]


async def test_the_host_keeps_its_signal_handlers(tmp_path: Path, fake_claude: str):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    before = signal.getsignal(signal.SIGINT)
    async with Server(tmp_path, claude=fake_claude):
        assert signal.getsignal(signal.SIGINT) is before
    assert signal.getsignal(signal.SIGINT) is before


def test_session_handles_compare_by_log_id():
    a, b = Session(None, "x"), Session(None, "x")  # type: ignore[arg-type]
    assert a == b and hash(a) == hash(b) and a != Session(None, "y")  # type: ignore[arg-type]


# -- queues, workflows and schedules (slice 2) ------------------------------


async def test_a_task_runs_on_a_worker_and_returns_its_result(srv: Server):
    task = await srv.queues.enqueue("work", "/md task done")
    info = await task.wait(timeout=20)
    assert isinstance(info, TaskInfo)
    assert (info.status, info.result, info.queue) == ("completed", "task done", "work")
    assert info.worker is not None
    assert await srv.queues.get(task.id) == task
    with pytest.raises(TaskNotFound):
        await srv.queues.get("task-nope")


async def test_a_pending_task_cancels(srv: Server):
    busy = await srv.queues.enqueue("work", "/busy 30")
    queued = await srv.queues.enqueue("work", "/md never")
    assert (await queued.info()).status == "pending"  # max_workers: 1
    assert (await queued.cancel()).status == "cancelled"
    assert (await busy.cancel()).status == "cancelled"


HELLO = """id: hello
title: Say hello
say: Print hello.
root:
  sh: "echo hello"
  id: greet
  say: Print hello.
"""

GATED = """id: gated
title: Ask, then print
say: Ask the main agent, then print.
root:
  seq:
    - gate: {by: main, ask: "Go on?"}
      id: ok
      say: Ask the main agent to go on.
    - sh: "echo after"
      id: after
      say: Print after.
  say: Ask, then print.
"""


async def test_a_workflow_runs_once_approved(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(HELLO, owner=owner, approve=True)
    info = await run.wait(timeout=20)
    assert isinstance(info, RunInfo)
    assert info.status == "done"
    assert [(n.id, n.state, n.summary) for n in info.nodes] == [
        ("greet", "done", "exit 0: hello")
    ]
    assert await run.owner() == owner
    assert run in await srv.workflows.list()


async def test_a_rejected_workflow_never_runs(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(HELLO, owner=owner)
    assert (await run.info()).status == "armed"
    assert (await run.reject()).status == "rejected"


async def test_wait_stops_at_a_gate_and_answer_passes_it(srv: Server):
    owner = await srv.sessions.spawn("/md ready")
    await owner.wait(timeout=20)
    run = await srv.workflows.arm(GATED, owner=owner, approve=True)
    info = await run.wait(timeout=20)
    assert info.status == "running"
    assert [o.kind for o in info.open] == ["gate"]
    await run.answer(info.open[0].to, choice="continue")
    info = await run.wait(timeout=20)
    assert info.status == "done"


async def test_a_schedule_is_created_fired_paused_and_deleted(srv: Server):
    sch = await srv.schedules.create("hourly-tick", every="1h", prompt="/md tick")
    info = await sch.info()
    assert isinstance(info, ScheduleInfo)
    assert (info.clock, info.paused) == ({"every": "1h"}, None)
    assert info.next is not None
    before = len(await srv.sessions.list())
    assert await sch.fire()
    assert len(await srv.sessions.list()) == before + 1
    assert (await sch.pause()).paused is not None
    assert (await sch.resume()).paused is None
    with pytest.raises(ValueError):
        await srv.schedules.create(
            "bad-clock", every="1h", cron="0 6 * * *", prompt="x"
        )
    await sch.delete()
    with pytest.raises(ScheduleNotFound):
        await srv.schedules.get("hourly-tick")


# -- a host mounts the client under a prefix (plan 2026-11-08, slice a1) ------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
async def host_srv(tmp_path: Path, fake_claude: str):
    """A Server for a host: named people, a prefix, and the host's origin."""
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    port = free_port()
    async with Server(
        tmp_path,
        claude=fake_claude,
        name="lab",
        policy=Named(),
        prefix="/aegis",
        origins=[f"http://127.0.0.1:{port}"],
    ) as s:
        yield s, port


async def serve_host(
    srv: Server, port: int, app: Starlette | None = None
) -> tuple[uvicorn.Server, asyncio.Task]:
    """A host app with aegis mounted, on the port the fixture named."""
    host = app or Starlette(
        routes=[
            Route("/", lambda _r: PlainTextResponse("host page")),
            Mount("/aegis", srv.asgi),
        ]
    )
    server = uvicorn.Server(
        uvicorn.Config(host, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(1000):
        if server.started:
            return server, task
        await asyncio.sleep(0.01)
    raise AssertionError("the host app did not start")


async def test_asgi_mounts_at_the_hosts_prefix(host_srv):
    srv, port = host_srv
    server, task = await serve_host(srv, port)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            page = await c.get("/aegis/")
            assert page.status_code == 200
            assert 'href="/aegis/static/css/client.css"' in page.text
            entry = await c.get("/aegis/aegis.js")
            assert entry.status_code == 200
            assert entry.headers["content-type"].startswith("text/javascript")
            manifest = (await c.get("/aegis/manifest.json")).json()
            assert manifest["prefix"] == "/aegis"
        async with connect(
            f"ws://127.0.0.1:{port}/aegis/ws",
            origin=f"http://127.0.0.1:{port}",
        ) as ws:
            await ws.send(
                json.dumps(
                    {"t": "hello", "token": srv.token_for(Who("ana")), "proto": PROTO}
                )
            )
            welcome = json.loads(await ws.recv())
            assert welcome["t"] == "welcome" and welcome["me"]["name"] == "ana"
    finally:
        server.should_exit = True
        await task


async def test_the_hosts_mount_serves_no_mcp_while_the_loopback_listener_does(
    host_srv,
):
    """Harness children reach the tools on the Server's listener at /mcp; the
    app a host mounts at /aegis must not put them on its public origin."""
    srv, port = host_srv
    server, task = await serve_host(srv, port)
    try:
        payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            gone = await c.post("/aegis/mcp", json=payload)
            assert gone.status_code == 404
        async with httpx.AsyncClient() as c:
            here = await c.post(
                f"{srv.base_url}/mcp",
                json=payload,
                headers={"accept": "application/json, text/event-stream"},
            )
            assert here.status_code == 200
            assert here.json()["result"]["tools"]
        s = await srv.as_who(Who("ana")).sessions.spawn("/mcp meta {}")
        turn = await s.wait(timeout=20)
        assert "mcp ok:" in turn.text
    finally:
        server.should_exit = True
        await task


async def test_a_prefixed_server_keeps_the_loopback_sign_in_url(host_srv):
    srv, _ = host_srv
    assert srv.url == f"{srv.base_url}/?token={srv.token}"
    assert srv.token_for(Who("ana")) != srv.token


async def test_a_host_signs_the_browser_in_for_sent_files(host_srv, tmp_path: Path):
    """A minted token is enough for the websocket, but a sent file is fetched
    over HTTP, which cannot carry that hello. The host sets the same session
    cookie the standalone sign-in does; without it a narrowing policy refuses
    the file."""
    from aegis import files

    srv, port = host_srv
    who = Who("ana")
    sid = (await srv.as_who(who).sessions.spawn()).log_id
    src = tmp_path / "host-note.txt"
    src.write_text("a note for the browser")
    rec = files.store(srv.app.roots.state_root, src, log_id=sid)
    path = "/aegis" + files.url(rec["file_id"], rec["name"])
    token = srv.token_for(who)

    async def page(request):
        return srv.sign_in(PlainTextResponse("host page"), token, request)

    host = Starlette(
        routes=[Route("/", page), Mount("/aegis", srv.asgi)],
    )
    server, task = await serve_host(srv, port, app=host)
    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            refused = await c.get(path)
            assert refused.status_code == 404
            signed = await c.get("/")
            assert signed.status_code == 200
            cookie = signed.headers["set-cookie"].lower()
            assert "httponly" in cookie and "samesite=strict" in cookie
            got = await c.get(path)
            assert got.status_code == 200
            assert got.content == b"a note for the browser"
    finally:
        server.should_exit = True
        await task


async def test_the_host_fixture_spawns_or_reuses_one_session(host_srv, tmp_path: Path):
    from .host_fixture import read_or_spawn

    srv, _ = host_srv
    first = await read_or_spawn(srv, Who("ana"), tmp_path / "session")
    again = await read_or_spawn(srv, Who("ana"), tmp_path / "session")
    assert (first, again) == (first, first)
    assert await srv.as_who(Who("ana")).sessions.get(first) is not None
