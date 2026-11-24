# ruff: noqa: F811 (pytest fixtures imported from other test modules)
"""The web harness's private bridge retains Chrome's real grant authority."""

import asyncio
import importlib
from types import SimpleNamespace

import pytest

from aegis.chrome import CallError, Chrome

from .conftest import until
from .test_chrome_driving import Driven
from .test_agents import world  # noqa: F401


def setup_bridge(tmp_path, answer=None):
    manager_type = importlib.import_module("aegis.web_agent.transport").WebBridges
    chrome = Chrome(tmp_path, lambda *_: None)
    records = []
    session = SimpleNamespace(
        log_id="web-one",
        handle="research-sidekick",
        spec=SimpleNamespace(harness="web-qwen"),
        record_card=records.append,
    )
    manager = manager_type(chrome)
    chrome.on_answer = manager.answered
    ext = Driven(
        chrome,
        answer
        or (
            lambda message: {
                "url": "https://chat.qwen.ai/c/test",
                "messages": [],
                "busy": False,
            }
        ),
    )
    return chrome, manager, manager.for_session(session), ext, records


async def grant(chrome, ext, bridge, *, url="https://chat.qwen.ai/", log_id="web-one"):
    task = asyncio.create_task(bridge("start", {}))
    await until(lambda: bool(chrome.requests))
    rid = next(iter(chrome.requests))
    await ext.say(
        t="grant",
        request=rid,
        log_id=log_id,
        tab={"name": "qwen", "title": "Qwen", "url": url},
    )
    return task


async def test_a_fresh_session_opens_its_tab_with_scripts_without_a_consent_card(
    tmp_path,
):
    chrome, manager, bridge, ext, records = setup_bridge(tmp_path)
    task = await grant(chrome, ext, bridge)
    got = await task
    assert got["tab"] == "qwen"
    assert ext.last("request")["request"]["kind"] == "open"
    assert ext.last("answer") == {
        "t": "answer",
        "request": ext.last("request")["request"]["id"],
        "give": True,
    }
    assert ext.last("eval") == {"t": "eval", "tab": "qwen", "on": True}
    assert chrome.tabs["qwen"]["eval"] is True
    assert not any(r["kind"] == "chrome_request" for r in records)
    assert manager.handles("web-one") is True


async def test_a_grant_to_another_session_cannot_back_the_web_agent(tmp_path):
    chrome, _, bridge, ext, _ = setup_bridge(tmp_path)
    task = await grant(chrome, ext, bridge, log_id="other-session")
    with pytest.raises(CallError, match="not_granted"):
        await task
    assert ext.calls == []


async def test_manual_control_and_scripts_off_prevent_page_actions(tmp_path):
    chrome, _, bridge, ext, _ = setup_bridge(tmp_path)
    await (await grant(chrome, ext, bridge))
    await chrome.scripts("qwen", False)
    await chrome.scripts("qwen", False)
    with pytest.raises(CallError, match="scripts_off"):
        await bridge("observe", {})
    chrome.tabs["qwen"]["eval"] = True
    chrome.tabs["qwen"]["holder"] = "person"
    with pytest.raises(CallError, match="person_has_control"):
        await bridge("send", {"text": "Do not send this"})
    assert ext.calls == []


async def test_navigation_to_another_chat_is_refused_before_submission(tmp_path):
    chrome, _, bridge, ext, _ = setup_bridge(tmp_path)
    await (await grant(chrome, ext, bridge))
    chrome.tabs["qwen"].update(eval=True, url="https://chat.qwen.ai/c/other")
    with pytest.raises(CallError, match="conversation_changed"):
        await bridge(
            "send",
            {"text": "Do not send this", "url": "https://chat.qwen.ai/c/original"},
        )
    assert ext.calls == []


async def test_a_revoked_grant_never_reaches_the_browser(tmp_path):
    chrome, _, bridge, ext, _ = setup_bridge(tmp_path)
    await (await grant(chrome, ext, bridge))
    chrome.tabs["qwen"]["eval"] = True
    await ext.say(t="revoke", tab="qwen", log_id="web-one")
    with pytest.raises(CallError, match="revoked"):
        await bridge("observe", {})
    assert ext.calls == []


async def test_the_private_bridge_does_not_accept_model_supplied_javascript(tmp_path):
    _, _, bridge, ext, _ = setup_bridge(tmp_path)
    with pytest.raises(ValueError, match="operation"):
        await bridge("eval", {"expression": "document.cookie"})
    assert ext.calls == []


async def test_the_host_binds_web_consent_without_delivering_it_as_a_model_task(world):
    from dataclasses import replace

    session = await world.spawn()
    session.spec = replace(session.spec, harness="web-qwen")
    bridge = world.app.sessions.web_for(session)
    chrome = world.app.chrome
    ext = Driven(chrome, lambda _: {})
    task = await grant(chrome, ext, bridge, log_id=session.log_id)
    await task
    assert session.held == []
    assert not any(e["kind"] == "chrome_request" for e in session.entries())
    assert chrome.tabs["qwen"]["eval"] is True


async def test_the_person_can_focus_a_backing_tab_while_holding_it(world):
    session = await world.spawn()
    ext = Driven(world.app.chrome, lambda _: {"focused": True})
    await ext.say(
        t="grant",
        log_id=session.log_id,
        tab={"name": "qwen", "url": "https://chat.qwen.ai/", "title": "Qwen"},
    )
    world.app.chrome.tabs["qwen"]["holder"] = "person"
    result = await world.app.registry.call(
        "chrome.focus", {"tab": "qwen", "log_id": session.log_id}
    )
    assert result == {"focused": True}
    assert ext.calls[-1]["op"] == "focus"
    assert ext.calls[-1]["log_id"] == session.log_id


async def test_agents_cannot_use_the_persons_focus_action(world):
    from aegis.ops import Caller, OpError

    session = await world.spawn()
    with pytest.raises(OpError, match="not_for_agents"):
        await world.app.registry.call(
            "chrome.focus",
            {"tab": "qwen", "log_id": session.log_id},
            Caller("agent", session.log_id),
        )


@pytest.mark.parametrize("submitted", [False, True])
async def test_deepseek_reports_the_actual_send_button_result(tmp_path, submitted):
    manager_type = importlib.import_module("aegis.web_agent.transport").WebBridges
    chrome = Chrome(tmp_path, lambda *_: None)
    session = SimpleNamespace(
        log_id="web-one",
        handle="research",
        spec=SimpleNamespace(harness="web-deepseek"),
        record_card=lambda _: None,
    )
    manager = manager_type(chrome)
    chrome.on_answer = manager.answered
    bridge = manager.for_session(session)
    ext = Driven(chrome, lambda _: {"value": {"submitted": submitted}})
    await (await grant(chrome, ext, bridge, url="https://chat.deepseek.com/"))
    chrome.tabs["qwen"]["eval"] = True
    if submitted:
        assert await bridge("send", {"text": "A task"}) == {"submitted": True}
    else:
        with pytest.raises(CallError, match="submission_refused"):
            await bridge("send", {"text": "A task that did not get submitted"})


async def test_observer_unwraps_the_actual_extension_eval_envelope(tmp_path):
    page = {
        "url": "https://chat.qwen.ai/c/test",
        "messages": [],
        "users": [],
        "busy": False,
    }
    chrome, _, bridge, ext, _ = setup_bridge(tmp_path, lambda _: {"value": page})
    await (await grant(chrome, ext, bridge))
    chrome.tabs["qwen"]["eval"] = True
    assert await bridge("observe", {}) == page


@pytest.mark.slow
async def test_native_web_bash_calls_the_real_mcp_as_its_own_session(world):
    from .test_web_process import Browser

    other = await world.spawn()
    original_handle = other.handle
    browser = Browser(
        "aegis call help_search query=Chrome limit=1 && "
        "aegis call session_rename handle=web-bound-test title='Web tool authority'",
        final="The local tools completed with the web session identity.",
    )
    world.app.sessions.web_for = lambda session: browser
    spawned = await world.app.registry.call(
        "session.spawn", {"agent": "opus", "harness": "web-qwen", "model": "web"}
    )
    session = world.app.sessions.sessions[spawned["log_id"]]
    try:
        await session.send("Use the local Aegis tools")
        await until(
            lambda: session.status == "idle" and bool(browser.results),
            timeout=20,
            what="real web-session Bash and MCP calls",
        )
        assert session.handle == "web-bound-test"
        assert other.handle == original_handle
        assert '"hits"' in browser.results[0]
        assert "exit_code: 0" in browser.results[0]
        assert "Chrome" in browser.results[0]
        assert any(
            e["kind"] == "tool" and e["title"] == "Bash" and e["status"] == "ok"
            for e in session.entries()
        )
        assert not any(
            e["kind"] == "tool" and e["title"] == "help_search" for e in other.entries()
        )
        auth_path = session._proc._auth.path
        assert auth_path.exists()
    finally:
        await session.stop()
    assert not auth_path.exists()
