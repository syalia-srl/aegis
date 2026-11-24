import importlib
import json

from aegis.claude.stream import Init, Result, Text, ToolCall, ToolOutput


def test_web_events_fold_to_the_existing_harness_event_types_without_usage():
    parser = importlib.import_module("aegis.web_agent.stream").Parser()
    init = parser.feed(
        json.dumps(
            {
                "type": "init",
                "session_id": "web-one",
                "harness": "Qwen web",
                "cwd": "/tmp/project",
            }
        )
    )[0]
    assert isinstance(init, Init)
    assert init.harness == "Qwen web"
    assert init.model is None
    assert isinstance(
        parser.feed('{"type":"text","id":"answer-1","text":"A proposal"}')[0], Text
    )
    assert isinstance(
        parser.feed('{"type":"tool_call","id":"answer-1/0","code":"echo 3"}')[0],
        ToolCall,
    )
    assert isinstance(
        parser.feed(
            '{"type":"tool_output","id":"answer-1/0","text":"3","error":false}'
        )[0],
        ToolOutput,
    )
    result = parser.feed('{"type":"result","error":false,"stop_reason":"stop"}')[0]
    assert isinstance(result, Result)
    assert result.cost_usd is None
    assert result.context_window is None


def test_historical_bridge_echoes_are_not_person_messages():
    from aegis.transcript.entries import fold_records

    prompt = "Check Chrome\n\n> from aegis:notice · knowledge\nRead the help"
    result = "Aegis Bash result · execution answer/0\nexit_code: 0\nstdout:\nreal output\n\nstderr:\n"
    events = [
        {"type": "echo", "text": prompt},
        {
            "type": "manual",
            "id": "user-1",
            "text": "You said Check Chrome…\n\n" + prompt,
        },
        {"type": "tool_call", "id": "answer/0", "code": "echo 'real output'"},
        {"type": "tool_output", "id": "answer/0", "text": result, "error": False},
        {
            "type": "manual",
            "id": "user-2",
            "text": "You said Aegis Bash result…\n\n" + result,
        },
        {
            "type": "manual",
            "id": "user-3",
            "text": "You said Aegis Bash result…\n\n" + result,
        },
        {"type": "manual", "id": "human-1", "text": "Also research the alternative"},
    ]
    records = [
        dict(i=i, ts=1000 + i, src="web", line=json.dumps(event))
        for i, event in enumerate(events)
    ]
    entries = fold_records(records).entries()
    users = [e for e in entries if e["kind"] == "user"]
    assert [e["md"] for e in users] == [
        "Check Chrome",
        "From Chrome:\n\nAlso research the alternative",
    ]
    notices = [e for e in entries if e["kind"] == "inbox"]
    assert len(notices) == 2
    assert "knowledge" in notices[0]["title"]
    assert "Bash result" in notices[1]["title"]
    assert "real output" in notices[1]["md"]
    assert all(not (e.get("detail") or {}).get("open") for e in notices)
    assert len(notices) == 2
    assert "knowledge" in notices[0]["title"]
    assert "web-bridge" in notices[1]["title"]
    assert "real output" in notices[1]["md"]
    assert not any((e.get("detail") or {}).get("open") for e in notices)


def test_a_real_chrome_message_can_repeat_an_aegis_prompt():
    from aegis.claude.stream import ExternalUser

    parser = importlib.import_module("aegis.web_agent.stream").Parser()
    parser.feed(json.dumps({"type": "echo", "text": "Try again"}))
    event = parser.feed(
        json.dumps({"type": "manual", "id": "human", "text": "Try again"})
    )[0]
    assert isinstance(event, ExternalUser)
    assert event.text == "Try again"
