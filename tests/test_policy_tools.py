"""aegis tools per session, and on_event (#959)."""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from aegis.ops import Caller, OpError
from aegis.policy import StandalonePolicy

from .conftest import until
from .test_agents import CONFIG, World, turn


class Chat(StandalonePolicy):
    def tools(self, who, session):
        return {"turn_end", "plan_update"}


class Audit(StandalonePolicy):
    def __init__(self) -> None:
        self.got: list[tuple[str, str]] = []

    async def on_event(self, who, event):
        self.got.append((event.kind, event.log_id))


class Slow(StandalonePolicy):
    async def on_event(self, who, event):
        await asyncio.sleep(5)
        raise RuntimeError("audit down")


@pytest.fixture
async def world_with(tmp_path, fake_claude):
    worlds = []

    async def make(policy):
        (tmp_path / ".aegis.yaml").write_text(CONFIG)
        w = World(tmp_path, fake_claude)
        w.policy = policy
        await w.start()
        worlds.append(w)
        return w

    yield make
    for w in worlds:
        await w.stop()


async def tool_names(w, s) -> set[str]:
    token = w.app.sessions.tokens._by_session[s.log_id]
    async with httpx.AsyncClient() as c:
        r = await c.post(
            w.base + "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={
                "accept": "application/json, text/event-stream",
                "x-aegis-session": token,
            },
        )
    return {t["name"] for t in r.json()["result"]["tools"]}


async def test_a_session_without_team_tools_neither_lists_nor_calls_them(world_with):
    w = await world_with(Chat())
    s = await w.spawn()
    assert await tool_names(w, s) == {"turn_end", "plan_update"}
    with pytest.raises(OpError) as e:
        await w.app.registry.call(
            "peer.handoff", {"target": "x", "context": "y"}, Caller("agent", s.log_id)
        )
    assert e.value.code == "not_for_agents"


async def test_a_session_under_the_default_lists_every_agent_tool(world_with):
    w = await world_with(StandalonePolicy())
    s = await w.spawn()
    assert len(await tool_names(w, s)) == len(w.app.registry.agent_ops())


async def test_events_arrive_in_order(world_with):
    audit = Audit()
    w = await world_with(audit)
    s = await w.spawn()
    await turn(s, "/md hi")
    await w.app.registry.call("session.close", {"log_id": s.log_id})
    await until(lambda: ("close", s.log_id) in audit.got, what="the close event")
    kinds = [k for k, i in audit.got if i == s.log_id]
    assert kinds[0] == "start" and "entry" in kinds and "turn_end" in kinds
    assert kinds.index("turn_end") < kinds.index("close")


async def test_a_raising_or_slow_on_event_never_delays_a_turn(world_with):
    # Review focus.
    w = await world_with(Slow())
    s = await w.spawn()
    t0 = time.monotonic()
    await turn(s, "/md hi")
    assert time.monotonic() - t0 < 2


class Brief(StandalonePolicy):
    def tools(self, who, session):
        return {"turn_end", "meta"}

    async def spawn(self, who, req):
        return req.replace(primer=False, system_prompt="You edit the document.")


async def test_meta_lists_only_the_sessions_tools(world_with):
    w = await world_with(Brief())
    s = await w.spawn()
    said = await w.app.registry.call("meta", {}, Caller("agent", s.log_id))
    assert "Tools: meta, turn_end." in said or "Tools: turn_end, meta." in said
    assert "plan_update" not in said


async def test_without_the_primer_the_prompt_is_the_hosts_alone(world_with):
    w = await world_with(Brief())
    s = await w.spawn()
    _, prompt = w.app.sessions.spawn_args(s)
    assert prompt == "You edit the document."
    said = await w.app.registry.call("meta", {}, Caller("agent", s.log_id))
    assert (
        said.startswith("You edit the document.") and "aegis, a workplace" not in said
    )


# -- agent scopes under aegis serve's policy (#1066) ---------------------------
SCOPED = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  fair:
    harness: claude-code
    model: opus
    effort: high
    permission: full
    tools: {deny: [workflows], queues: [general], agents: [opus]}
queues:
  general: {agent: opus}
  other: {agent: opus}
"""


@pytest.fixture
async def scoped(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(SCOPED)
    w = World(tmp_path, fake_claude)
    await w.start()
    yield w
    await w.stop()


async def mcp_call(w, s, tool: str, args: dict) -> dict:
    """A call as ``aegis call`` from the session's shell makes it: /mcp
    tools/call with the session's token."""
    token = w.app.sessions.tokens._by_session[s.log_id]
    async with httpx.AsyncClient() as c:
        r = await c.post(
            w.base + "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": args},
            },
            headers={
                "accept": "application/json, text/event-stream",
                "x-aegis-session": token,
                "x-aegis-shell": "1",
            },
        )
    return r.json()["result"]


async def spawn_fair(w):
    r = await w.app.registry.call("session.spawn", {"agent": "fair"})
    return w.app.sessions.sessions[r["log_id"]]


async def test_a_scope_denying_workflows_neither_lists_nor_calls_them(scoped):
    s = await spawn_fair(scoped)
    await until(lambda: s.log_id in scoped.app.tools_of, what="the tools")
    names = await tool_names(scoped, s)
    assert "workflow_arm" not in names and "plan_update" in names
    assert "turn_end" in names
    with pytest.raises(OpError) as e:
        await scoped.app.registry.call("workflow.list", {}, Caller("agent", s.log_id))
    assert e.value.code == "out_of_scope" and "workflow_arm" in e.value.message
    said = await mcp_call(scoped, s, "workflow_list", {})
    assert said["isError"] and "out_of_scope" in said["content"][0]["text"]


async def test_a_scope_refuses_a_queue_it_does_not_name(scoped):
    s = await spawn_fair(scoped)
    me = Caller("agent", s.log_id)
    with pytest.raises(OpError) as e:
        await scoped.app.registry.call(
            "queue.enqueue", {"queue": "other", "payload": "x"}, me
        )
    assert e.value.code == "out_of_scope" and "general" in e.value.message
    r = await scoped.app.registry.call(
        "queue.enqueue", {"queue": "general", "payload": "x", "callback": False}, me
    )
    assert r["task_id"]


async def test_a_scope_refuses_an_agent_it_does_not_name(scoped):
    s = await spawn_fair(scoped)
    me = Caller("agent", s.log_id)
    with pytest.raises(OpError) as e:
        await scoped.app.registry.call("session.spawn", {"agent": "fair"}, me)
    assert e.value.code == "out_of_scope" and "opus" in e.value.message
    with pytest.raises(OpError) as e:
        await scoped.app.registry.call("session.handoff", {}, me)
    assert e.value.code == "out_of_scope"
    other = await spawn_fair(scoped)
    with pytest.raises(OpError) as e:
        await scoped.app.registry.call(
            "peer.handoff", {"target": other.handle, "context": "hi"}, me
        )
    assert e.value.code == "out_of_scope"
    r = await scoped.app.registry.call("session.spawn", {"agent": "opus"}, me)
    assert r["log_id"]
