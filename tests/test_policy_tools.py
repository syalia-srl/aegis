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
