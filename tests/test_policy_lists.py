"""can on lists, channels and sent files (#959)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis import files
from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.policy_view import PolicyView
from aegis.roots import make_roots
from aegis.session import SpawnSpec

from .conftest import until
from .policies import Named, Who

ANA = Caller("user", principal=Principal("ana", Who("ana")))
BEN = Caller("user", principal=Principal("ben", Who("ben")))


def spec(cwd: Path, owner: str) -> SpawnSpec:
    return SpawnSpec(
        agent="opus",
        model="opus",
        effort="high",
        permission="full",
        cwd=cwd,
        owner=owner,
    )


@pytest.fixture
async def hidden(tmp_path: Path, fake_claude: str):
    app = App(
        make_roots(tmp_path, None),
        claude_bin=fake_claude,
        policy=Named(hidden={"ben": {"ana"}}),
    )
    ana_s = await app.sessions.spawn(spec(tmp_path, "ana"))
    ben_s = await app.sessions.spawn(spec(tmp_path, "ben"))
    yield SimpleNamespace(
        app=app, ana_s=ana_s.log_id, ben_s=ben_s.log_id, root=tmp_path
    )
    await app.shutdown()


def ids(rows) -> set[str]:
    rows = rows.get("items", rows.get("rows", rows)) if isinstance(rows, dict) else rows
    return {r.get("log_id") or r.get("handle") for r in rows}


def snapshot(app: App, channel: str, caller: Caller):
    got = []
    view = PolicyView(app, app.who(caller))
    sub = app.channels.subscribe(channel, got.append, view=view)
    return got, sub


async def test_the_sessions_channel_hides_what_the_caller_cannot_list(hidden):
    app = hidden.app
    got, _ = snapshot(app, "sessions", BEN)
    listed = ids(got[0]["data"])
    assert hidden.ben_s in listed and hidden.ana_s not in listed
    got, _ = snapshot(app, "sessions", ANA)
    assert hidden.ana_s in ids(got[0]["data"])


async def test_session_list_hides_what_the_caller_cannot_list(hidden):
    app = hidden.app
    handle = app.sessions.sessions[hidden.ana_s].handle
    listed = {r["handle"] for r in await app.registry.call("session.list", {}, BEN)}
    assert handle not in listed
    listed = {r["handle"] for r in await app.registry.call("session.list", {}, ANA)}
    assert handle in listed


async def test_the_archive_hides_what_the_caller_cannot_list(hidden):
    app = hidden.app
    await app.sessions.close(hidden.ana_s)
    page = await app.registry.call("archive.list", {}, BEN)
    assert hidden.ana_s not in ids(page)
    page = await app.registry.call("archive.list", {}, ANA)
    assert hidden.ana_s in ids(page)


async def test_a_hidden_transcript_cannot_be_subscribed(hidden):
    app = hidden.app
    with pytest.raises(OpError, match="unknown_channel"):
        snapshot(app, f"transcript:{hidden.ana_s}", BEN)
    snapshot(app, f"transcript:{hidden.ben_s}", BEN)


async def test_the_journal_reads_only_visible_sessions(hidden, monkeypatch):
    app = hidden.app
    assert app.visible_ids(app.who(BEN)) == {hidden.ben_s}
    own = app.visible_ids(app.who(Caller("user")))
    assert own is not None and {hidden.ana_s, hidden.ben_s} <= own


async def test_a_session_that_becomes_hidden_leaves_a_live_subscription(hidden):
    app = hidden.app
    got, _ = snapshot(app, "sessions", BEN)
    app.policy.hidden["ben"].add("ben")
    s = app.sessions.sessions[hidden.ben_s]
    s.title = "renamed"
    s._publish_now()

    def ops():
        return [op for m in got[1:] for op in m.get("ops", [])]

    await until(lambda: any(op.get("remove") == hidden.ben_s for op in ops()))
    assert not any(
        (op.get("upsert") or {}).get("log_id") == hidden.ben_s for op in ops()
    )


def test_a_sent_file_records_its_session(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("hi")
    rec = files.store(tmp_path / "state", src, log_id="s-1")
    assert files.session_of(tmp_path / "state", rec["file_id"]) == "s-1"
    rec = files.store(tmp_path / "state", src)
    assert files.session_of(tmp_path / "state", rec["file_id"]) is None


def test_a_sent_file_is_served_only_to_who_may_read_its_session(tmp_path):
    from starlette.testclient import TestClient

    from aegis.web import build_web

    app = App(make_roots(tmp_path, None), policy=Named(hidden={"ben": {"ana"}}))
    app.sessions.archived["s-ana"] = {"log_id": "s-ana", "owner": "ana"}
    src = tmp_path / "a.txt"
    src.write_text("hi")
    state = app.roots.state_root
    mine = files.store(state, src, log_id="s-ana")
    old = files.store(state, src)
    with TestClient(build_web(app, "t0ken", {"testserver"})) as c:

        def get(rec, token):
            url = f"/files/{rec['file_id']}/a.txt"
            return c.get(url, headers={"authorization": f"Bearer {token}"}).status_code

        assert get(mine, "tok-ben") == 404
        assert get(mine, "tok-ana") == 200
        assert get(old, "tok-ana") == 404
        assert get(old, "t0ken") == 200
