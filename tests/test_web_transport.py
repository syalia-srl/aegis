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


def setup_bridge(tmp_path):
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
        lambda message: {
            "url": "https://chat.qwen.ai/c/test",
            "messages": [],
            "busy": False,
        },
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


async def test_a_fresh_session_requests_a_new_tab_and_keeps_the_consent_card(tmp_path):
    chrome, manager, bridge, ext, records = setup_bridge(tmp_path)
    task = await grant(chrome, ext, bridge)
    got = await task
    assert got["tab"] == "qwen"
    assert ext.last("request")["request"]["kind"] == "open"
    assert records[0]["kind"] == "chrome_request"
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
    assert any(
        e["kind"] == "chrome_request" and e["status"] == "granted"
        for e in session.entries()
    )


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


async def test_deepseek_cannot_report_submitted_when_its_send_button_is_disabled(
    tmp_path,
):
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
    ext = Driven(chrome, lambda _: {"submitted": False})
    await (await grant(chrome, ext, bridge, url="https://chat.deepseek.com/"))
    chrome.tabs["qwen"]["eval"] = True
    with pytest.raises(CallError, match="submission_refused"):
        await bridge("send", {"text": "A task that did not get submitted"})
