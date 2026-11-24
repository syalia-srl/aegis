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
