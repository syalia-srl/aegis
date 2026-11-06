"""A stand-in for ``pi --mode rpc``: answers the commands PiProcess sends and
plays one short turn per prompt, in pi 1.5.0's shapes
(``fixtures/pi/rpc-turn.jsonl``). Its argv goes to ``$FAKE_PI_ARGV``.

A prompt that contains ``slow`` keeps its turn open until ``abort``.
"""

import json
import os
import sys

if os.environ.get("FAKE_PI_ARGV"):
    with open(os.environ["FAKE_PI_ARGV"], "w") as f:
        json.dump(
            {"argv": sys.argv[1:], "agent_dir": os.environ.get("PI_CODING_AGENT_DIR")},
            f,
        )


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def answer(cmd, data=None, ok=True):
    msg = {
        "type": "response",
        "id": cmd.get("id"),
        "command": cmd["type"],
        "success": ok,
    }
    if data is not None:
        msg["data"] = data
    out(msg)


def assistant(text, stop="stop", error=None):
    m = {
        "role": "assistant",
        "content": [{"type": "text", "text": text}] if text else [],
        "usage": {"input": 10, "output": 5, "cacheRead": 2, "cacheWrite": 0},
        "stopReason": stop,
    }
    if error:
        m["errorMessage"] = error
    return m


model = {
    "provider": "fake",
    "id": "m1",
    "name": "Fake M1",
    "contextWindow": 1000,
    "reasoning": True,
}
steers = []
open_turn = False
for raw in sys.stdin:
    cmd = json.loads(raw)
    t = cmd["type"]
    if t == "get_state":
        answer(
            cmd,
            {
                "model": model,
                "sessionFile": "/tmp/fake-pi/s.jsonl",
                "isStreaming": open_turn,
            },
        )
    elif t == "get_available_models":
        answer(
            cmd,
            {"models": [model, {"provider": "fake", "id": "m2", "name": "Fake M2"}]},
        )
    elif t == "get_session_stats":
        answer(
            cmd, {"cost": 0.25, "contextUsage": {"tokens": 17, "contextWindow": 1000}}
        )
    elif t in ("set_model", "set_thinking_level"):
        answer(cmd, ok=cmd.get("provider") != "nope")
    elif t == "steer":
        steers.append(cmd["message"])
        answer(cmd)
    elif t == "prompt":
        answer(cmd)
        out({"type": "agent_start"})
        out(
            {
                "type": "message_update",
                "assistantMessageEvent": {
                    "type": "text_delta",
                    "contentIndex": 0,
                    "delta": "hi",
                },
            }
        )
        if "slow" in cmd["message"]:
            open_turn = True
            continue
        m = assistant("hi " + " ".join(steers))
        out({"type": "message_end", "message": m})
        out({"type": "agent_end", "messages": [m]})
    elif t == "abort":
        answer(cmd)
        if open_turn:
            open_turn = False
            m = assistant("", "error", "This operation was aborted")
            out({"type": "message_end", "message": m})
            out({"type": "agent_end", "messages": [m]})
    else:
        answer(cmd, ok=False)
