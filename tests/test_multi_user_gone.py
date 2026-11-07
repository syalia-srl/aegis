"""aegis 3.0 removed the multi-user mode (#958): a state directory that still
holds its tokens refuses to start rather than run with them ignored."""

import pytest

from aegis.ops import Principal
from aegis.tokens import OldStateError, TokenStore, refuse_old_state


def test_a_state_with_member_tokens_refuses_to_start(tmp_path):
    (tmp_path / "tokens.json").write_text(
        '{"tokens": [{"name": "ben", "role": "member", "sha256": "x"}]}'
    )
    with pytest.raises(OldStateError) as e:
        refuse_old_state(tmp_path)
    assert "tokens.json" in str(e.value) and "multi-user" in str(e.value)


def test_an_empty_tokens_file_or_none_boots(tmp_path):
    refuse_old_state(tmp_path)
    (tmp_path / "tokens.json").write_text("{}")
    refuse_old_state(tmp_path)
    (tmp_path / "tokens.json").write_text('{"tokens": []}')
    refuse_old_state(tmp_path)


def test_only_the_admin_token_resolves(tmp_path):
    store = TokenStore(tmp_path, owner="ana")
    assert store.resolve(store.admin_token()) == Principal("ana")
    assert store.resolve("not-the-token") is None
    assert store.resolve(None) is None


def test_prepare_refuses_old_state_with_the_message(tmp_path):
    from aegis.server import StartError, prepare

    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    state = tmp_path / ".aegis" / "state"
    state.mkdir(parents=True)
    (state / "tokens.json").write_text('{"tokens": [{"name": "ben"}]}')
    with pytest.raises(StartError) as e:
        prepare(tmp_path, tmp_path)
    assert "multi-user" in str(e.value)


def test_ambient_capabilities_left_by_an_old_unit_refuse_to_start(
    tmp_path, monkeypatch
):
    from aegis import server
    from aegis.server import StartError, prepare

    (tmp_path / ".aegis.yaml").write_text("agents: {}\n")
    monkeypatch.setattr(server, "_proc_status", lambda: "CapAmb:\t00000000000000c0\n")
    with pytest.raises(StartError) as e:
        prepare(tmp_path, tmp_path)
    assert "capabilities" in str(e.value) and "aegis service install" in str(e.value)
    monkeypatch.setattr(server, "_proc_status", lambda: "CapAmb:\t0000000000000000\n")
    prepare(tmp_path, tmp_path)


async def test_boot_drops_former_members_push_subscriptions(tmp_path, fake_claude):
    import json

    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    push = tmp_path / ".aegis" / "state" / "push"
    push.mkdir(parents=True)
    import getpass

    me = getpass.getuser()
    rows = [
        {"id": "p_1", "owner": me, "endpoint": "https://a", "keys": {}},
        {"id": "p_2", "owner": "ben", "endpoint": "https://b", "keys": {}},
    ]
    (push / "subscriptions.json").write_text(json.dumps(rows))
    w = await World(tmp_path, fake_claude).start()
    try:
        assert [r["id"] for r in w.app.push.subscriptions.list()] == ["p_1"]
    finally:
        await w.stop()


async def test_a_former_members_session_and_terminal_are_archived_at_boot(
    tmp_path, fake_claude
):
    import json

    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    s = await w.spawn()
    log_id = s.log_id
    meta_path = w.app.sessions.metas.path(log_id)
    t = await w.app.terminals.spawn("shell", w.app.user)
    term_meta = w.app.terminals.dir / t.id / "meta.json"
    await w.stop()
    meta = json.loads(meta_path.read_text())
    meta.update(owner="ben", linux_user="ben", cwd="/home/ben")
    meta_path.write_text(json.dumps(meta))
    tm = json.loads(term_meta.read_text())
    tm.update(owner="ben", linux_user="ben")
    term_meta.write_text(json.dumps(tm))
    w = await World(tmp_path, fake_claude).start()
    try:
        assert log_id not in w.app.sessions.sessions
        assert w.app.sessions.archived[log_id].get("archived")
        assert t.id not in w.app.terminals.open
        assert w.app.terminals.archived[t.id]["reason"] == "multi_user_removed"
    finally:
        await w.stop()


async def test_an_agent_in_a_former_members_session_acts_as_the_one_person(
    tmp_path, fake_claude
):
    import json

    from aegis.ops import Caller

    from .test_agents import CONFIG, World

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    s = await w.spawn()
    log_id = s.log_id
    meta_path = w.app.sessions.metas.path(log_id)
    await w.stop()
    meta = json.loads(meta_path.read_text())
    meta["owner"] = "ben"  # a second token's session, with no Linux user
    meta_path.write_text(json.dumps(meta))
    w = await World(tmp_path, fake_claude).start()
    try:
        assert log_id in w.app.sessions.sessions
        assert w.app.who(Caller("agent", log_id=log_id)) == Principal(w.app.user)
    finally:
        await w.stop()
