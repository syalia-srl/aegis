"""notices: false keeps aegis's own messages out of a product agent (#983)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.app import App
from aegis.notice_sources import Item
from aegis.policy import StandalonePolicy
from aegis.roots import make_roots
from aegis.transcript.store import read_store

from .conftest import until

CONFIG = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
"""


class Quiet(StandalonePolicy):
    def __init__(self, notices: bool) -> None:
        self.notices = notices

    async def spawn(self, who, req):
        return req.replace(notices=self.notices)


@pytest.fixture
async def make(tmp_path: Path, fake_claude: str):
    apps = []

    async def build(notices: bool):
        (tmp_path / ".aegis.yaml").write_text(CONFIG)
        app = App(make_roots(tmp_path, None), claude_bin=fake_claude, policy=Quiet(notices))
        apps.append(app)
        r = await app.registry.call("session.spawn", {"agent": "opus"})
        s = app.sessions.sessions[r["log_id"]]
        await until(lambda: s.running, what="the fake to start")
        return SimpleNamespace(app=app, s=s)

    yield build
    for app in apps:
        await app.shutdown()


def sends(s) -> list[str]:
    return [
        r["text"]
        for r in read_store(s.store.path)[0]
        if r.get("kind") == "send" and isinstance(r.get("text"), str)
    ]


async def idle(s) -> None:
    await until(lambda: s.status == "idle", what="the turn to end")


async def test_a_quiet_session_gets_no_notice_blocks_and_no_aegis_messages(make):
    w = await make(notices=False)
    w.app.notices.set(w.s.log_id, "disk", [Item("full", "warn", "The disk is full.", "", False)])
    assert w.app.sessions.notice_blocks(w.s, "tool") == ""
    await w.s.deliver("> from aegis:cascade · now", "Switched models.")
    await w.s.deliver("> from monitor:m1 · ok", "The build finished.")
    await idle(w.s)
    text = "\n".join(sends(w.s))
    assert "The build finished." in text
    assert "The disk is full." not in text and "> from aegis:" not in text


async def test_a_quiet_sessions_restart_continues_under_a_neutral_header(make):
    w = await make(notices=False)
    w.s.held = [{"header": "> from aegis:restart · now", "body": "Go on.", "ts": 0.0}]
    await w.s._flush_held()
    await idle(w.s)
    (text,) = sends(w.s)
    assert text.startswith("> continue\nGo on.")


async def test_a_default_session_still_gets_them(make):
    w = await make(notices=True)
    w.app.notices.set(w.s.log_id, "disk", [Item("full", "warn", "The disk is full.", "", False)])
    await w.s.deliver("> from aegis:cascade · now", "Switched models.")
    await idle(w.s)
    text = "\n".join(sends(w.s))
    assert "> from aegis:cascade" in text and "The disk is full." in text
