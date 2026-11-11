# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Consent per tab (chrome.py, chrome_ops.py): an agent sees only the tabs
granted to it, asks with browser_request or browser_open(where="person"), and
is woken with granted or denied; the person answers and revokes from the
extension or from aegis, and the other side follows."""

from __future__ import annotations

import json

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import inbox, mcp, turn, world  # noqa: F401  (fixture)


class Ext:
    """A paired extension's socket, as the server sees it."""

    def __init__(self, chrome, did="c-test"):
        self.chrome, self.did, self.got = chrome, did, []
        chrome.devices[did] = {
            "id": did,
            "name": "Chrome",
            "token_sha": "x",
            "paired_at": 0,
            "last_seen": 0,
        }

        async def send(msg):
            self.got.append(msg)

        async def close():
            pass

        self.send = send
        chrome.connected(did, send, close, "0.2.0")

    async def say(self, **msg):
        return await self.chrome.handle(self.did, msg)

    def last(self, t):
        return next(m for m in reversed(self.got) if m["t"] == t)


def said(out: str) -> dict | list:
    assert out.startswith("mcp ok: "), out
    return json.loads(out.removeprefix("mcp ok: "))


def card(s, rid):
    return next(e for e in s.entries() if e["id"] == f"cr-{rid}")


TAB = {"name": "qwen", "title": "Qwen Chat", "url": "https://chat.qwen.ai/"}


async def test_no_browser_connected_is_chrome_unavailable(world):
    a = await world.spawn()
    out = await turn(a, mcp("browser_request", match="qwen.ai", why="ask it"))
    assert "chrome_unavailable" in out


async def test_a_request_given_in_the_popup_wakes_the_agent_with_its_tab(world):
    a = await world.spawn()
    ext = Ext(world.app.chrome)
    got = said(await turn(a, mcp("browser_request", match="qwen.ai", why="ask it")))
    rid = got["request"]
    assert (
        got["state"] == "asked" and ext.last("request")["request"]["match"] == "qwen.ai"
    )
    assert card(a, rid)["status"] == "asked"
    assert said(await turn(a, mcp("browser_list"))) == []
    await ext.say(t="grant", tab=TAB, log_id=a.log_id, request=rid)
    await until(lambda: inbox(a), timeout=8, what="the wake")
    assert "chrome:qwen · granted" in json.dumps(inbox(a), ensure_ascii=False)
    assert card(a, rid)["status"] == "granted"
    tabs = said(await turn(a, mcp("browser_list")))
    assert tabs == [
        {
            "id": "chrome:qwen",
            "kind": "person",
            "title": "Qwen Chat",
            "url": "https://chat.qwen.ai/",
        }
    ]
    assert world.app.chrome.requests == {}


async def test_a_refusal_in_aegis_tells_the_extension_and_the_agent(world):
    a = await world.spawn()
    ext = Ext(world.app.chrome)
    rid = said(
        await turn(
            a,
            mcp(
                "browser_open",
                url="https://chat.deepseek.com",
                where="person",
                why="compare",
            ),
        )
    )["request"]
    assert ext.last("request")["request"]["kind"] == "open"
    await world.app.registry.call("chrome.answer", {"request": rid, "give": False})
    assert ext.last("cancel") == {"t": "cancel", "request": rid}
    await until(lambda: inbox(a), timeout=8, what="the wake")
    assert "chrome · denied" in json.dumps(inbox(a), ensure_ascii=False)
    assert card(a, rid)["status"] == "denied"
    with pytest.raises(OpError) as e:
        await world.app.registry.call("chrome.answer", {"request": rid, "give": True})
    assert e.value.code == "gone"


async def test_give_in_aegis_asks_the_extension_to_pick_the_tab(world):
    a = await world.spawn()
    ext = Ext(world.app.chrome)
    rid = said(await turn(a, mcp("browser_request", match="qwen", why="")))["request"]
    await world.app.registry.call("chrome.answer", {"request": rid, "give": True})
    assert ext.last("answer") == {"t": "answer", "request": rid, "give": True}
    # Not answered until the extension grants: the card still waits.
    assert card(a, rid)["status"] == "asked"


async def test_revoking_in_aegis_reaches_the_extension_and_hides_the_tab(world):
    a = await world.spawn()
    ext = Ext(world.app.chrome)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    assert world.app.chrome.wire()["devices"][0]["tabs"] == 1
    await world.app.registry.call("chrome.revoke", {"tab": "qwen", "log_id": a.log_id})
    assert ext.last("revoke") == {"t": "revoke", "tab": "qwen", "log_id": a.log_id}
    assert said(await turn(a, mcp("browser_list"))) == []
    assert world.app.chrome.wire()["tabs"] == []


async def test_a_grant_is_one_session_and_the_extension_revokes_one(world):
    a, b = await world.spawn(), await world.spawn()
    ext = Ext(world.app.chrome)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    assert said(await turn(b, mcp("browser_list"))) == []
    await ext.say(t="grant", tab=TAB, log_id=b.log_id)
    await ext.say(t="revoke", tab="qwen", log_id=a.log_id)
    assert said(await turn(a, mcp("browser_list"))) == []
    assert [t["id"] for t in said(await turn(b, mcp("browser_list")))] == [
        "chrome:qwen"
    ]
    # Closing the tab ends every grant on it.
    await ext.say(t="revoke", tab="qwen", log_id=None)
    assert world.app.chrome.tabs == {}


async def test_the_sessions_a_tab_can_go_to_and_a_hello_resyncs(world):
    a = await world.spawn()
    ext = Ext(world.app.chrome)
    reply = await ext.say(t="sessions", id=7)
    assert reply["id"] == 7 and a.log_id in [s["log_id"] for s in reply["sessions"]]
    world.app.chrome.sync_tabs(
        ext.did,
        [
            {**TAB, "grants": [a.log_id]},
            {"name": "x", "title": "", "url": "", "grants": []},
        ],
    )
    assert list(world.app.chrome.tabs) == ["qwen"]
    await world.app.chrome.unpair(ext.did)
    assert world.app.chrome.tabs == {}


async def test_an_agent_cannot_answer_or_revoke(world):
    a = await world.spawn()
    Ext(world.app.chrome)
    out = await turn(a, mcp("chrome_answer", request="rq-x", give=True))
    assert "mcp ok" not in out
