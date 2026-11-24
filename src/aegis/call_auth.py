"""Private, process-lived credentials for harnesses that filter shell tokens.

Only the file path goes into the shell environment. The session token keeps
its existing MCP environment reference; it never enters argv or a transcript.
Like the ordinary session environment, the file grants the session's power
to children running as the same OS user, and is removed when the child ends.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

AUTH_VAR = "AEGIS_CALL_AUTH"


class CallAuth:
    def __init__(self, mcp: tuple[str, str]) -> None:
        self._dir = tempfile.TemporaryDirectory(prefix="aegis-call-")
        self.path = Path(self._dir.name) / "auth.json"
        with open(
            self.path, "x", opener=lambda p, flags: os.open(p, flags, 0o600)
        ) as f:
            json.dump({"url": mcp[0], "token": mcp[1]}, f)

    def close(self) -> None:
        self._dir.cleanup()


def read_auth(path: str) -> tuple[str, str] | None:
    try:
        with Path(path).open() as f:
            data = json.loads(f.read(16384))
        if isinstance(data, dict):
            url, token = data.get("url"), data.get("token")
            if isinstance(url, str) and url and isinstance(token, str) and token:
                return url, token
    except (OSError, ValueError):
        pass
    return None
