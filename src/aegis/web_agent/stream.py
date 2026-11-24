"""The web driver's JSON records as the transcript's existing typed events."""

from __future__ import annotations

import json

from ..claude.stream import (
    Echo,
    ExternalUser,
    Garbled,
    Ignored,
    Init,
    Result,
    Text,
    ToolCall,
    ToolOutput,
)


class Parser:
    def __init__(self) -> None:
        self.session_id: str | None = None
        self.model: str | None = None
        self.cwd: str | None = None
        self._submitted: dict[str, str | None] = {}
        self._bridge_notices: set[str] = set()

    def end_turn(self) -> None:
        pass

    def feed(self, line: str) -> list:
        try:
            obj = json.loads(line)
        except ValueError:
            return [Garbled(raw=line)]
        if not isinstance(obj, dict):
            return [Garbled(raw=line)]
        kind = obj.get("type")
        if kind == "init":
            self.session_id, self.model, self.cwd = (
                obj.get("session_id"),
                obj.get("model"),
                obj.get("cwd"),
            )
            return [
                Init(
                    self.session_id,
                    self.model,
                    None,
                    harness=obj.get("harness", "Web"),
                    cwd=self.cwd,
                )
            ]
        if kind == "manual":
            text = str(obj["text"])
            # Older Gemini stores include the accessibility preview and nested
            # copies of prompts we submitted. Repair their presentation when
            # replaying history, without treating arbitrary Chrome text as ours.
            body = text.split("\n", 1)[-1] if text.startswith("You said ") else text
            key = " ".join(body.split())
            if text.startswith("You said ") and key in self._submitted:
                result = self._submitted[key]
                if result is None or key in self._bridge_notices:
                    return [Ignored(type="bridge_echo")]
                self._bridge_notices.add(key)
                return [
                    Echo(
                        text="> from aegis:notice · web-bridge · Bash result\n\n"
                        + result
                    )
                ]
            return [ExternalUser(str(obj["id"]), text, "Chrome")]
        if kind == "echo":
            text = str(obj.get("text") or "")
            self._submitted[" ".join(text.split())] = None
            return [Echo(text=text)]
        if kind == "text":
            return [
                Text(
                    text=str(obj.get("text") or ""),
                    key=obj.get("id"),
                    parent=None,
                    usage=None,
                )
            ]
        if kind == "tool_call":
            return [
                ToolCall(
                    id=obj["id"],
                    name="Bash",
                    input={"command": obj["code"]},
                    parent=None,
                    usage=None,
                )
            ]
        if kind == "tool_output":
            self._submitted[" ".join(obj["text"].split())] = obj["text"]
            return [
                ToolOutput(
                    id=obj["id"],
                    text=obj["text"],
                    is_error=bool(obj.get("error")),
                    parent=None,
                )
            ]
        if kind == "result":
            error = bool(obj.get("error"))
            return [
                Result(
                    is_error=error,
                    subtype="error" if error else "success",
                    duration_ms=obj.get("duration_ms"),
                    cost_usd=None,
                    stop_reason=obj.get("stop_reason"),
                    context_window=None,
                    error_text=obj.get("text") if error else None,
                )
            ]
        return [Ignored(type=str(kind))]
