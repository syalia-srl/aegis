"""MCP servers as configuration (#960): the ``mcp:`` map, an agent's list,
and what each start gets."""

from __future__ import annotations

import pytest
from ruamel.yaml import YAML

from aegis.config import AgentDoc, ConfigDoc, doc_from, stamp_of, validate, write
from aegis.mcp_servers import by_ref, expand, parse
from aegis.ops import OpError
from aegis.policy import McpServer

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
