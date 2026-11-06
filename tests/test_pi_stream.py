"""The recorded run (``fixtures/pi/rpc-turn.jsonl``) through the parser.

The capture is raw stdout of ``pi --mode rpc`` 1.5.0 over two turns: the
first reads, edits and writes a file, runs ``ls``, calls two aegis tools
through MCP and takes a steer mid-turn; the second starts ``sleep 30`` and is
aborted. The test keeps the lines process.py stores (it answers responses,
drops the events in ``DROPPED`` and the user messages, and replaces
``agent_end`` with ``aegis/turn_done``), wraps them in aegis's own lines and
folds the store back.
"""

import json
from pathlib import Path

from aegis.claude.stream import Delta, Result, Step
from aegis.pi.process import DROPPED
from aegis.pi.stream import Parser
from aegis.transcript.entries import fold_records

FIXTURE = Path(__file__).parent / "fixtures" / "pi" / "rpc-turn.jsonl"
SESSION = "/home/ana/.local/state/aegis/pi/sessions/20261105-ana/s.jsonl"


def events() -> list[dict]:
    return [json.loads(line) for line in FIXTURE.read_text().splitlines()]


def own(method: str, **params) -> str:
    return json.dumps({"method": method, "params": params})


def stored() -> list[str]:
    """The lines a session keeps for the two turns, as process.py hands
    them over: the deltas are folded live and never stored."""
    out = [
        own(
            "aegis/session",
            session_id=SESSION,
            model="opencode-go/m",
            cwd="/home/ana/proj",
        )
    ]
    prompts = iter(["edit the notes", "run sleep"])
    out.append(own("aegis/echo", text=next(prompts)))
    for ev in events():
        kind = ev.get("type")
        if kind in DROPPED or kind in (
            "response",
            "message_update",
            "tool_execution_update",
        ):
            continue
        if kind == "message_end" and ev["message"].get("role") != "assistant":
            continue
        if kind == "agent_end":
            last = ev["messages"][-1]
            stop = last.get("stopReason")
            out.append(
                own(
                    "aegis/turn_done",
                    stop_reason=stop,
                    duration_ms=8000,
                    cost_usd=0.0015,
                )
            )
            if stop == "stop":
                out.append(own("aegis/echo", text=next(prompts)))
            continue
        out.append(json.dumps(ev))
    return out


def records() -> list[dict]:
    return [
        {"i": i, "ts": 1000.0 + i, "src": "pi", "line": line}
        for i, line in enumerate(stored())
    ]


def test_the_recorded_run_folds_back_into_entries():
    entries = fold_records(records()).entries()
    assert any(e["summary"] == "pi · opencode-go/m" for e in entries)
    assert [e["md"] for e in entries if e["kind"] == "user"] == [
        "edit the notes",
        "run sleep",
    ]

    tools = [e for e in entries if e["kind"] == "tool"]
    names = [e["title"] for e in tools]
    assert len(tools) == 7, names
    read = next(e for e in tools if e["detail"].get("path", "").endswith("notes.txt"))
    assert read["status"] == "ok"
    # the aborted sleep is the one tool that failed
    assert [e["status"] for e in tools].count("err") == 1

    prose = [e["md"] for e in entries if e["kind"] == "prose"]
    assert any("BANANA" in p for p in prose)  # the steer reached the answer
    assert [e for e in entries if e["kind"] == "thinking"]


def test_a_turn_done_line_carries_cost_and_the_abort():
    p = Parser()
    (ok,) = p.feed(
        own("aegis/turn_done", stop_reason="stop", cost_usd=0.0015, context_window=1000)
    )
    assert isinstance(ok, Result) and not ok.is_error
    assert ok.cost_usd == 0.0015 and ok.context_window == 1000
    (stopped,) = p.feed(own("aegis/turn_done", stop_reason="interrupted"))
    assert stopped.is_error and stopped.subtype == "interrupted"
    (failed,) = p.feed(
        own("aegis/turn_done", stop_reason="error", error="410: model deprecated")
    )
    assert failed.subtype == "error" and failed.error_text == "410: model deprecated"


def test_every_update_is_a_delta_so_none_is_stored():
    p = Parser()
    for ev in events():
        if ev.get("type") in ("message_update", "tool_execution_update"):
            out = p.feed(json.dumps(ev))
            assert len(out) == 1 and isinstance(out[0], Delta)


def test_deltas_and_the_whole_message_share_a_key():
    p = Parser()
    keys: set[str] = set()
    for ev in events():
        out = p.feed(json.dumps(ev))
        if ev.get("type") == "message_update":
            keys |= {d.key for d in out if d.key}
        if ev.get("type") == "message_end" and ev["message"].get("role") == "assistant":
            whole = {getattr(e, "key", None) for e in out} - {None}
            assert whole <= keys or not whole
            keys.clear()


def test_an_assistant_message_reports_its_usage():
    p = Parser()
    first = next(
        ev for ev in events()
        if ev.get("type") == "message_end" and ev["message"].get("role") == "assistant"
    )  # fmt: skip
    steps = [e for e in p.feed(json.dumps(first)) if isinstance(e, Step)]
    assert steps and steps[0].usage.context > 0
