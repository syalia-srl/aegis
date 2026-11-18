"""pi's RPC events, one stdout line at a time, as typed events.

The events are ``claude/stream.py``'s, so the fold keeps one set of rules for
every harness. Measured on pi 1.5.0 (fixture ``tests/fixtures/pi/rpc-turn.jsonl``):

- A line is an ``aegis/*`` line process.py wrote, or one of pi's events,
  ``{type: ...}``. process.py keeps pi's answers (``type: response``) and the
  events that carry nothing for the transcript.
- ``message_update`` streams an assistant message as
  ``assistantMessageEvent`` ``{type: text_delta | thinking_delta, contentIndex,
  delta}``; its ``message_end`` carries the whole message. Neither names the
  message (no id, and the update carries no timestamp), so its parts are keyed
  by a count of assistant messages that ends on each ``message_end``. Only
  those lines are stored, so a re-fold counts the same.
- Every ``message_update`` is a ``Delta`` (an empty one draws nothing), so the
  session never stores one.
- A provider's failure is an assistant message with ``stopReason: "error"``
  and an ``errorMessage``, not a failed command. An abort ends the same way,
  with ``This operation was aborted``.
- A tool is ``tool_execution_start`` ``{toolCallId, toolName, args}`` then
  ``tool_execution_end`` ``{result: {content}, isError}``.
- The turn ends with ``agent_end``, which process.py replaces with its own
  ``aegis/turn_done`` once ``get_session_stats`` has answered with the cost.
"""

from __future__ import annotations

import json
from typing import Any

from ..claude.stream import (
    Delta,
    Echo,
    Event,
    Garbled,
    Ignored,
    Init,
    Result,
    Step,
    Text,
    Thinking,
    ToolCall,
    ToolOutput,
    Usage,
)
from .config import tool_name

LABEL = "pi"
OWN = "aegis/"
_DELTAS = {"text_delta": "prose", "thinking_delta": "thinking"}


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(c.get("text") or "")
        for c in content
        if isinstance(c, dict) and c.get("type") == "text" and c.get("text")
    )


def _usage(u: dict) -> Usage | None:
    if not u:
        return None
    return Usage(
        input=int(u.get("input") or 0),
        cache_creation=int(u.get("cacheWrite") or 0),
        cache_read=int(u.get("cacheRead") or 0),
        output=int(u.get("output") or 0),
    )


class Parser:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.cwd: str | None = None
        # Assistant messages ended so far: the open one's parts are keyed by
        # the next number.
        self.messages = 0
        self.error: str | None = None

    def end_turn(self) -> None:
        self.error = None

    def _key(self, index: Any) -> str:
        return f"m{self.messages + 1}.{index if isinstance(index, int) else 0}"

    def feed(self, line: str) -> list[Event]:
        try:
            obj: Any = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        method = str(obj.get("method") or "")
        if method.startswith(OWN):
            handler = getattr(self, "_" + method.partition("/")[2], None)
            return (
                handler(_dict(obj.get("params"))) if handler else [Ignored(type=method)]
            )
        kind = str(obj.get("type") or "")
        handler = getattr(self, "_e_" + kind, None)
        return handler(obj) if handler else [Ignored(type=kind)]

    # -- aegis's own lines ---------------------------------------------
    def _session(self, p: dict) -> list[Event]:
        self.session_id = _str(p.get("session_id"))
        self.model = _str(p.get("model"))
        self.cwd = _str(p.get("cwd"))
        return [self._init()]

    def _init(self) -> Init:
        return Init(
            session_id=self.session_id,
            model=self.model,
            version=None,
            harness=LABEL,
            cwd=self.cwd,
        )

    def _turn(self, p: dict) -> list[Event]:
        """The model changed mid-session (process.set)."""
        model = _str(p.get("model"))
        if not model or model == self.model:
            return []
        self.model = model
        return [self._init()]

    def _echo(self, p: dict) -> list[Event]:
        text = str(p.get("text") or "")
        return [Echo(text=text)] if text else []

    def _turn_done(self, p: dict) -> list[Event]:
        """The turn's last line. ``stop_reason`` is the last assistant
        message's (``stop``, ``length``, ``error``) or ``interrupted`` when the
        person stopped it."""
        stop = str(p.get("stop_reason") or "stop")
        span, cost, window = (
            p.get("duration_ms"),
            p.get("cost_usd"),
            p.get("context_window"),
        )
        ok = stop == "stop"
        error = None if ok else (_str(p.get("error")) or self.error)
        self.end_turn()
        return [
            Result(
                is_error=not ok,
                subtype="success"
                if ok
                else "interrupted"
                if stop == "interrupted"
                else stop,
                duration_ms=span if isinstance(span, int) else None,
                cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
                stop_reason="end_turn" if ok else None,
                context_window=window if isinstance(window, int) else None,
                error_text=error,
            )
        ]

    # -- pi's events ---------------------------------------------------
    def _e_message_update(self, obj: dict) -> list[Event]:
        ev = _dict(obj.get("assistantMessageEvent"))
        kind = _DELTAS.get(str(ev.get("type") or ""))
        if kind is None:
            return [Delta(key="", kind="", text="")]
        return [
            Delta(
                key=self._key(ev.get("contentIndex")),
                kind=kind,
                text=str(ev.get("delta") or ""),
            )
        ]

    def _e_message_end(self, obj: dict) -> list[Event]:
        msg = _dict(obj.get("message"))
        if msg.get("role") != "assistant":
            return []  # the prompt and steers are aegis/echo lines
        out: list[Event] = []
        content = msg.get("content")
        for i, block in enumerate(content if isinstance(content, list) else []):
            b = _dict(block)
            if b.get("type") == "text" and str(b.get("text") or "").strip():
                out.append(
                    Text(text=str(b["text"]), parent=None, usage=None, key=self._key(i))
                )
            elif b.get("type") == "thinking" and str(b.get("thinking") or "").strip():
                out.append(
                    Thinking(
                        text=str(b["thinking"]),
                        parent=None,
                        usage=None,
                        key=self._key(i),
                    )
                )
        usage = _usage(_dict(msg.get("usage")))
        if usage is not None and usage.context:
            out.append(Step(usage=usage))
        if msg.get("stopReason") == "error":
            self.error = _str(msg.get("errorMessage"))
        self.messages += 1
        return out

    def _e_tool_execution_start(self, obj: dict) -> list[Event]:
        tid = _str(obj.get("toolCallId"))
        if not tid:
            return []
        name, inp = tool_name(
            str(obj.get("toolName") or "tool"), _dict(obj.get("args"))
        )
        return [ToolCall(id=tid, name=name, input=inp, parent=None, usage=None)]

    def _e_tool_execution_update(self, obj: dict) -> list[Event]:
        return [Delta(key="", kind="", text="")]  # the end carries the whole output

    def _e_tool_execution_end(self, obj: dict) -> list[Event]:
        tid = _str(obj.get("toolCallId"))
        if not tid:
            return []
        result = _dict(obj.get("result"))
        return [
            ToolOutput(
                id=tid,
                text=_text(result.get("content")),
                is_error=bool(obj.get("isError")),
                parent=None,
            )
        ]
