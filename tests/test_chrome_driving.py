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

from .conftest import until
from .test_agents import inbox, mcp, turn, world  # noqa: F401  (fixture)
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

    delay = 0.0
    busy = 0
    most = 0

    async def reply(self, msg, answer):
        self.busy += 1
        self.most = max(self.most, self.busy)
        await asyncio.sleep(self.delay)
        self.busy -= 1
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
    out = await turn(
        a, mcp("browser_resize", browser="chrome:qwen", width=400, height=800)
    )
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


async def test_a_handover_gives_the_tab_to_the_person_until_done(world):
    a, b = await world.spawn(), await world.spawn()
    chrome = world.app.chrome
    ext = Driven(chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    await ext.say(t="grant", tab=TAB, log_id=b.log_id)
    out = await turn(
        a,
        mcp("browser_handover", browser="chrome:qwen", reason="Solve the captcha"),
    )
    assert "holds chrome:qwen" in out
    told = ext.last("hold")
    assert (told["holder"], told["ask"], told["by"]) == (
        "person",
        "Solve the captcha",
        a.handle,
    )
    assert chrome.wire()["tabs"][0]["holder"] == "person"
    # Reads go on; actions wait, and never reach the browser.
    await turn(b, mcp("browser_snapshot", browser="chrome:qwen"))
    calls = len(ext.calls)
    out = await turn(b, mcp("browser_click", browser="chrome:qwen", target="text=Go"))
    assert "person_has_control" in out and "Solve the captcha" in out
    assert len(ext.calls) == calls
    await world.app.registry.call("chrome.release", {"tab": "qwen"})
    assert ext.last("hold")["holder"] == "agent"
    for s in (a, b):
        await until(lambda s=s: inbox(s), timeout=8, what="the wake")
        assert "chrome:qwen · back" in json.dumps(inbox(s), ensure_ascii=False)


async def test_pause_in_the_ribbon_refuses_actions_and_resume_wakes_who_tried(world):
    a = await world.spawn()
    chrome = world.app.chrome
    ext = Driven(chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    await ext.say(t="hold", tab="qwen", holder="person")
    out = await turn(
        a, mcp("browser_type", browser="chrome:qwen", target="#q", text="x")
    )
    assert "person_has_control" in out
    sent = len(ext.got)
    await ext.say(t="hold", tab="qwen", holder="agent")
    assert len(ext.got) == sent  # the extension said so: no echo
    await until(lambda: inbox(a), timeout=8, what="the wake")
    log = said(await turn(a, mcp("browser_events", browser="chrome:qwen")))
    assert any("pause" in e for e in log["events"])


async def test_a_reconnecting_browser_keeps_a_tab_held(world):
    a = await world.spawn()
    chrome = world.app.chrome
    ext = Driven(chrome, page)
    chrome.sync_tabs(
        ext.did, [TAB | {"grants": [a.log_id], "holder": "person", "ask": "Sign in"}]
    )
    out = await turn(a, mcp("browser_click", browser="chrome:qwen", target="text=Go"))
    assert "person_has_control" in out and "Sign in" in out


async def test_actions_on_one_tab_run_one_at_a_time(world):
    a, b = await world.spawn(), await world.spawn()
    ext = Driven(world.app.chrome, page)
    ext.delay = 0.3
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    await ext.say(t="grant", tab=TAB, log_id=b.log_id)
    await asyncio.gather(
        turn(a, mcp("browser_click", browser="chrome:qwen", target="text=A")),
        turn(b, mcp("browser_click", browser="chrome:qwen", target="text=B")),
    )
    assert len(ext.calls) == 2 and ext.most == 1


async def test_eval_runs_in_the_tab_and_returns_its_json(world):
    a = await world.spawn()
    ext = Driven(world.app.chrome, lambda m: {"value": {"n": 2}})
    await ext.say(t="grant", tab=TAB | {"eval": True}, log_id=a.log_id)
    out = said(
        await turn(a, mcp("browser_eval", browser="chrome:qwen", expression="1+1"))
    )
    assert out == {"n": 2}
    assert ext.calls[-1]["op"] == "eval" and ext.calls[-1]["args"] == {
        "expression": "1+1"
    }


async def test_the_scripts_switch_goes_from_aegis_to_the_extension_and_back(world):
    a = await world.spawn()
    chrome = world.app.chrome
    ext = Driven(chrome, page)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    assert chrome.wire()["tabs"][0]["eval"] is False
    await world.app.registry.call("chrome.scripts", {"tab": "qwen", "on": True})
    assert ext.last("eval") == {"t": "eval", "tab": "qwen", "on": True}
    assert chrome.wire()["tabs"][0]["eval"] is True
    sent = len(ext.got)
    await ext.say(t="eval", tab="qwen", on=False)
    assert chrome.wire()["tabs"][0]["eval"] is False and len(ext.got) == sent
    # A reconnecting browser says what it holds.
    chrome.sync_tabs(ext.did, [TAB | {"grants": [a.log_id], "eval": True}])
    assert chrome.wire()["tabs"][0]["eval"] is True


async def test_an_op_an_old_extension_does_not_know_says_it_is_outdated(world):
    from aegis.chrome import shipped_version

    def old(msg):
        raise ValueError("not_supported", f"{msg['op']} does not work yet")

    a = await world.spawn()
    ext = Driven(world.app.chrome, old)
    await ext.say(t="grant", tab=TAB, log_id=a.log_id)
    dev = world.app.chrome.devices[ext.did]
    dev["version"] = "0.4.0"
    out = await turn(a, mcp("browser_eval", browser="chrome:qwen", expression="1"))
    assert "extension_outdated" in out and "Settings → Chrome" in out
    dev["version"] = shipped_version()
    out = await turn(a, mcp("browser_eval", browser="chrome:qwen", expression="1"))
    assert "not_supported" in out and "extension_outdated" not in out
