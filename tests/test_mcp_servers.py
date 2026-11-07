"""MCP servers as configuration (#960): the ``mcp:`` map, an agent's list,
and what each start gets."""

from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from aegis.app import App
from aegis.claude.harness import ClaudeCode
from aegis.config import AgentDoc, ConfigDoc, doc_from, stamp_of, validate, write
from aegis.mcp_servers import by_ref, expand, parse
from aegis.ops import OpError
from aegis.policy import McpServer, StandalonePolicy
from aegis.roots import make_roots

from .conftest import until

AGENT = {
    "harness": "claude-code",
    "model": "opus",
    "effort": "low",
    "permission": "read",
}


def test_an_entry_is_http_or_stdio_and_both_or_neither_is_named():
    servers, errors = parse({
        "notes": {"url": "http://x/mcp", "headers": {"A": "b"}},
        "phone": {"command": "uv", "args": ["run", "x"], "env": {"K": "v"}},
        "both": {"url": "http://y", "command": "z"},
        "none": {"args": ["a"]},
        "aegis": {"url": "http://z"},
    })  # fmt: skip
    assert servers["notes"] == McpServer.http("notes", "http://x/mcp", {"A": "b"})
    assert servers["phone"] == McpServer.stdio("phone", "uv", ["run", "x"], {"K": "v"})
    assert "url or a command, not both" in errors["both"]
    assert "a url or a command" in errors["none"]
    assert "reserved" in errors["aegis"]
    assert set(servers) == {"notes", "phone"}


def test_a_field_of_the_wrong_kind_is_an_error_on_its_entry():
    _, errors = parse({
        "a": {"url": "http://x", "env": {"K": "v"}},
        "b": {"command": "x", "args": "run"},
        "c": {"command": "x", "colour": "red"},
        "d": "http://x",
    })  # fmt: skip
    assert "env" in errors["a"]
    assert "args" in errors["b"]
    assert "colour" in errors["c"]
    assert "map" in errors["d"]
    assert parse(None) == ({}, {})


def test_expand_reads_the_environment_and_names_a_missing_variable():
    s = McpServer.http("notes", "http://x", {"Authorization": "Bearer ${NOTES_TOKEN}"})
    assert expand(s, {"NOTES_TOKEN": "t1"}).headers == {"Authorization": "Bearer t1"}
    with pytest.raises(OpError) as e:
        expand(s, {})
    assert e.value.code == "mcp_env" and "${NOTES_TOKEN}" in e.value.message


def test_by_ref_moves_every_value_into_the_environment():
    s = McpServer.stdio("phone", "uv", [], {"PHONE_STATE": "/var/x"})
    (out,), env = by_ref([s])
    assert out.env == {"PHONE_STATE": "${AEGIS_MCP_PHONE_PHONE_STATE}"}
    assert env == {"AEGIS_MCP_PHONE_PHONE_STATE": "/var/x"}


def test_an_agent_naming_an_unknown_server_is_a_config_error():
    doc = ConfigDoc(agents=[AgentDoc(name="clerk", mcp=["ghost"], **AGENT)])
    (f,) = [f for f in validate(doc) if f.row == "agents.clerk"]
    assert "ghost" in f.message


def test_an_agent_mcp_that_is_not_a_list_stops_the_agent():
    from aegis.agents import agents_from

    (a,) = agents_from({"agents": {"clerk": AGENT | {"mcp": "notes"}}})
    assert a.error and "mcp" in a.error
    (b,) = agents_from({"agents": {"clerk": AGENT | {"mcp": ["notes"]}}})
    assert b.error is None and b.mcp == ("notes",)


FILE = """\
# servers this box offers
mcp:
  notes:  # the notes service
    url: http://notes/mcp
    headers: {Authorization: "Bearer ${NOTES_TOKEN}"}
  phone:
    command: uv
    args: [run, phone]
    env: {PHONE_STATE: /var/phone}
agents:
  clerk:
    harness: claude-code
    model: opus
    effort: low
    permission: read
    mcp: [notes, phone]  # both
"""


def test_the_mcp_block_round_trips_and_one_edit_keeps_the_rest(tmp_path):
    p = tmp_path / ".aegis.yaml"
    p.write_text(FILE)
    doc = doc_from(YAML(typ="safe").load(FILE))
    assert [m.name for m in doc.mcp] == ["notes", "phone"]
    assert doc.agents[0].mcp == ["notes", "phone"]
    assert validate(doc) == []
    assert write(p, doc, stamp_of(p)) == []
    assert p.read_text() == FILE
    doc.mcp[0].headers["Authorization"] = "Bearer ${OTHER}"
    assert write(p, doc, stamp_of(p)) == []
    text = p.read_text()
    assert YAML(typ="safe").load(text) == YAML(typ="safe").load(
        FILE.replace("${NOTES_TOKEN}", "${OTHER}")
    )
    assert "# the notes service" in text and "headers: {Authorization:" in text


# -- what each start gets (task 2) ---------------------------------------------

START = """\
default_agent: clerk
mcp:
  notes: {url: "http://notes/mcp", headers: {Authorization: "Bearer ${NOTES_TOKEN}"}}
  phone: {command: uv, args: [run, phone]}
agents:
  clerk: {harness: claude-code, model: opus, effort: low, permission: full, mcp: [notes, prog]}
"""


class Host(StandalonePolicy):
    def __init__(self, servers=()):
        self.servers = list(servers)

    def mcp(self, who, session):
        return list(self.servers)


async def started(tmp_path, fake_claude, policy=None, config=START, **mcp):
    (tmp_path / ".aegis.yaml").write_text(config)
    app = App(
        make_roots(tmp_path, None),
        claude_bin=fake_claude,
        policy=policy or Host(),
        mcp={"prog": McpServer.stdio("prog", "x")} | mcp,
    )
    r = await app.registry.call("session.spawn", {"agent": "clerk"})
    return app, app.sessions.sessions[r["log_id"]]


async def stopped(app, s):
    await s.send("/md hi")
    await until(lambda: s.resume_id is not None, timeout=10, what="a first turn")
    await app.registry.call("session.stop", {"log_id": s.log_id})
    await until(lambda: not s.running, what="the stop")


@pytest.fixture(autouse=True)
def notes_token(monkeypatch):
    monkeypatch.setenv("NOTES_TOKEN", "t1")


async def test_a_start_gets_the_agents_servers_then_the_policys(tmp_path, fake_claude):
    host = Host([McpServer.http("host", "http://h/mcp")])
    app, s = await started(tmp_path, fake_claude, host)
    try:
        got = app.mcp_for(s)
        assert [m.name for m in got] == ["notes", "prog", "host"]
        assert got[0].headers == {"Authorization": "Bearer t1"}
    finally:
        await app.shutdown()


async def test_a_name_from_two_sources_refuses_the_start(tmp_path, fake_claude):
    app, s = await started(tmp_path, fake_claude)
    try:
        app.policy.servers = [McpServer.http("notes", "http://other")]
        with pytest.raises(OpError) as e:
            app.mcp_for(s)
        assert e.value.code == "mcp_conflict"
        assert ".aegis.yaml" in e.value.message and "the host policy" in e.value.message
        app.policy.servers = [McpServer.http("aegis", "http://other")]
        with pytest.raises(OpError, match="named aegis"):
            app.mcp_for(s)
    finally:
        await app.shutdown()


async def test_an_unknown_name_refuses_the_start(tmp_path, fake_claude):
    app, s = await started(tmp_path, fake_claude)
    try:
        app.mcp = {}
        with pytest.raises(OpError) as e:
            app.mcp_for(s)
        assert e.value.code == "unknown_mcp"
        assert e.value.message == (
            "agent clerk names MCP server prog, which is not defined"
        )
    finally:
        await app.shutdown()


async def test_an_unset_variable_refuses_the_start_and_says_so(
    tmp_path, fake_claude, monkeypatch
):
    app, s = await started(tmp_path, fake_claude)
    try:
        await stopped(app, s)
        monkeypatch.delenv("NOTES_TOKEN")
        with pytest.raises(OpError) as e:
            await app.registry.call("session.send", {"log_id": s.log_id, "text": "hi"})
        assert e.value.code == "mcp_env"
        assert not s.running
        assert any(
            x["kind"] == "error" and "${NOTES_TOKEN}" in x["summary"]
            for x in s.entries()
        )
    finally:
        await app.shutdown()


async def test_a_transport_the_harness_cannot_take_refuses_by_name(
    tmp_path, fake_claude, monkeypatch
):
    app, s = await started(tmp_path, fake_claude)
    try:
        monkeypatch.setattr(ClaudeCode, "mcp_transports", frozenset({"http"}))
        with pytest.raises(OpError) as e:
            app.mcp_for(s)
        assert e.value.code == "mcp_transport"
        assert "prog" in e.value.message and "stdio" in e.value.message
    finally:
        await app.shutdown()


async def test_an_edited_entry_applies_at_the_next_start(tmp_path, fake_claude):
    app, s = await started(tmp_path, fake_claude)
    try:
        await stopped(app, s)
        launches = []
        real = s.harness.process
        s.harness.process = lambda launch: launches.append(launch) or real(launch)
        (tmp_path / ".aegis.yaml").write_text(
            START.replace("http://notes/", "http://new/")
        )
        await until(
            lambda: app.config.current().mcp["notes"].url == "http://new/mcp",
            what="the config reload",
        )
        await s.send("/md again")
        await until(lambda: s.running, what="the restart")
        assert launches[0].extra_mcp[0].url == "http://new/mcp"
    finally:
        await app.shutdown()
