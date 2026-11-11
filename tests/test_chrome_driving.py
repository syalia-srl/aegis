# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""Driving a person's tab (chrome.py, chrome_ops.py): the browser_* tools on a
chrome:<name> id go to the tab's browser as a call and come back as its
result, after the server checks the grant; the tab's log keeps the agents'
actions and the page's events."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

from .test_agents import mcp, turn, world  # noqa: F401  (fixture)
from .test_chrome_consent import TAB, Ext, said


class Driven(Ext):
    """An extension that answers each call with what `answer` returns, or
    with the error it raises as (code, message)."""

    def __init__(self, chrome, answer):
        super().__init__(chrome)
        self.calls: list[dict] = []
        inner = self.send

        async def send(msg):
            await inner(msg)
            if msg["t"] == "call":
                self.calls.append(msg)
                asyncio.get_running_loop().create_task(self.reply(msg, answer))

        self.send = send
        chrome.sockets[self.did] = send

    async def reply(self, msg, answer):
        try:
            value = answer(msg)
            out = {"t": "result", "id": msg["id"], "ok": True, "value": value}
        except ValueError as e:
            code, message = e.args
            out = {"t": "result", "id": msg["id"], "ok": False, "code": code,
                   "message": message}  # fmt: skip
        await self.chrome.handle(self.did, out)


def page(msg):
    return {"url": TAB["url"], "title": TAB["title"]}


async def test_a_click_on_a_granted_tab_goes_to_its_browser(world):
    a = await world.spawn()
    ext = Driven(world.app.chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = said(
        await turn(a, mcp("browser_click", browser="chrome:qwen", target="role=button"))
    )
    assert out == {"url": TAB["url"], "title": TAB["title"], "events": []}
    call = ext.calls[-1]
    assert (call["tab"], call["log_id"], call["op"]) == ("qwen", a.log_id, "click")
    assert call["args"] == {"target": "role=button"}
    assert world.app.chrome.wire()["tabs"][0]["last"].endswith("click role=button")


async def test_another_sessions_tab_is_not_granted_and_never_reaches_the_browser(
    world,
):
    a, b = await world.spawn(), await world.spawn()
    ext = Driven(world.app.chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = await turn(b, mcp("browser_snapshot", browser="chrome:qwen"))
    assert "not_granted" in out
    assert ext.calls == []


async def test_a_grant_that_ended_answers_revoked(world):
    a = await world.spawn()
    ext = Driven(world.app.chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    await ext.say(t="revoke", tab="qwen", log_id=a.log_id)
    out = await turn(a, mcp("browser_snapshot", browser="chrome:qwen"))
    assert "revoked" in out and ext.calls == []


async def test_the_extension_refusing_a_call_reaches_the_agent(world):
    a = await world.spawn()

    def refuse(msg):
        raise ValueError("not_granted", "chrome:qwen is not granted to you")

    ext = Driven(world.app.chrome, refuse)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = await turn(a, mcp("browser_snapshot", browser="chrome:qwen"))
    assert "not_granted: chrome:qwen is not granted to you" in out


async def test_a_browser_that_disconnects_mid_call_is_chrome_unavailable(world):
    a = await world.spawn()
    chrome = world.app.chrome

    def hang(msg):
        chrome.disconnected(ext.did, chrome.sockets[ext.did])
        raise ValueError("never", "sent")

    ext = Driven(chrome, hang)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = await turn(a, mcp("browser_snapshot", browser="chrome:qwen"))
    assert "chrome_unavailable" in out
    assert chrome.calls == {}


async def test_typing_a_password_logs_its_length_not_its_text(world):
    a = await world.spawn()
    ext = Driven(world.app.chrome, lambda m: page(m) | {"secret": True})
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    await turn(
        a,
        mcp(
            "browser_type",
            browser="chrome:qwen",
            target="label=Password",
            text="hunter2",
        ),
    )
    log = said(await turn(a, mcp("browser_events", browser="chrome:qwen")))
    text = json.dumps(log)
    assert "hunter2" not in text and "7 characters" in text


async def test_the_pages_events_reach_the_agent_after_its_action(world):
    a = await world.spawn()
    chrome = world.app.chrome

    def navigates(msg):
        chrome.log(
            "qwen", {"kind": "navigate", "url": "https://chat.qwen.ai/c/1"}, "page"
        )
        return page(msg)

    ext = Driven(chrome, navigates)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = said(
        await turn(
            a,
            mcp("browser_goto", browser="chrome:qwen", url="https://chat.qwen.ai/c/1"),
        )
    )
    assert out["events"] == ["#1 page navigate https://chat.qwen.ai/c/1"]
    # An event for a tab of another browser is not this one's.
    await chrome.handle(
        "c-other", {"t": "event", "tab": "qwen", "event": {"kind": "x"}}
    )
    assert chrome.events("qwen")[0] == 2  # the navigation and the agent's goto


async def test_a_screenshot_lands_in_the_tabs_shots(world):
    a = await world.spawn()
    png = b"\x89PNG\r\n\x1a\nfake"
    ext = Driven(world.app.chrome, lambda m: {"png": base64.b64encode(png).decode()})
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = said(await turn(a, mcp("browser_screenshot", browser="chrome:qwen")))
    assert Path(out["path"]).read_bytes() == png
    assert "/chrome/qwen/shots/" in out["path"]


async def test_tools_for_later_slices_say_so(world):
    a = await world.spawn()
    ext = Driven(world.app.chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    out = await turn(a, mcp("browser_eval", browser="chrome:qwen", expression="1"))
    assert "not_supported" in out and ext.calls == []


async def test_a_later_tab_of_the_same_name_starts_a_clean_log(world):
    a = await world.spawn()
    chrome = world.app.chrome
    ext = Driven(chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    chrome.log("qwen", {"kind": "navigate", "url": "x"}, "page")
    await ext.say(t="revoke", tab="qwen", log_id=None)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    assert chrome.events("qwen") == (0, [])
