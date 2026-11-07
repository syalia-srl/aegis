"""Each driver writes a session's MCP servers in its harness's format, and
no header or env value lands in an argv or a file (#960)."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from aegis import launch as aegis_launch

from aegis.claude.harness import ClaudeSession
from aegis.codex.config import argv, child_env
from aegis.cursor.config import mcp_servers
from aegis.harness import Launch, harness_for
from aegis.mcp import mcp_config
from aegis.opencode.config import child_config
from aegis.pi import config as pi
from aegis.pi.config import agent_dir
from aegis.policy import McpServer

from .conftest import until

NOTES = McpServer.http("notes", "http://n/mcp", {"Authorization": "Bearer SECRET-1"})
PHONE = McpServer.stdio("phone", "uv", ["run", "phone"], {"PHONE_STATE": "SECRET-2"})
BOTH = (NOTES, PHONE)


def launch(tmp_path: Path, **kw) -> Launch:
    return Launch(
        cwd=tmp_path,
        model="opus",
        effort="low",
        permission=kw.pop("permission", "full"),
        resume_id=None,
        mcp=("http://a/mcp", "tok"),
        system_prompt=None,
        stderr_path=tmp_path / "logs" / "x.stderr",
        on_line=lambda line: None,
        on_exit=lambda code, tail: None,
        log_id="20261107-000000-abcdef",
        extra_mcp=BOTH,
        **kw,
    )


def test_claude_config_names_both_servers_and_holds_no_value():
    cfg = json.loads(mcp_config("http://a/mcp", BOTH))["mcpServers"]
    assert list(cfg) == ["aegis", "notes", "phone"]
    assert cfg["notes"] == {
        "type": "http",
        "url": "http://n/mcp",
        "headers": {"Authorization": "${AEGIS_MCP_NOTES_AUTHORIZATION}"},
    }
    assert cfg["phone"]["command"] == "uv" and cfg["phone"]["args"] == ["run", "phone"]
    assert "SECRET" not in json.dumps(cfg)


def test_a_claude_session_puts_values_in_its_env_and_allows_the_tools(tmp_path):
    proc = ClaudeSession("claude", launch(tmp_path))._proc
    assert "SECRET" not in " ".join(proc.argv)
    settings = json.loads(proc.argv[proc.argv.index("--settings") + 1])
    matcher = settings["hooks"]["PreToolUse"][0]["matcher"]
    assert matcher == "mcp__(aegis|notes|phone)__.*"
    assert proc.env is not None
    assert proc.env["AEGIS_MCP_NOTES_AUTHORIZATION"] == "Bearer SECRET-1"
    assert proc.env["AEGIS_MCP_PHONE_PHONE_STATE"] == "SECRET-2"


def test_opencode_config_has_both_servers_and_allows_their_tools():
    cfg = child_config(("http://a/mcp", "tok"), "read", servers=BOTH)
    servers = cfg["mcp"]["servers"]
    assert servers["notes"] == {
        "type": "remote",
        "url": "http://n/mcp",
        "headers": {"Authorization": "Bearer SECRET-1"},
        "oauth": False,
    }
    assert servers["phone"] == {
        "type": "local",
        "command": ["uv", "run", "phone"],
        "environment": {"PHONE_STATE": "SECRET-2"},
    }
    allowed = {r["action"] for r in cfg["permissions"] if r["effect"] == "allow"}
    assert {"aegis_*", "notes_*", "phone_*"} <= allowed


def test_codex_argv_holds_names_and_the_env_holds_values():
    out = argv("codex", "http://a/mcp", (), servers=BOTH)
    assert "SECRET" not in " ".join(out)
    assert 'mcp_servers.phone.env_vars=["PHONE_STATE"]' in out
    assert 'mcp_servers.phone.command="uv"' in out
    assert (
        'mcp_servers.notes.env_http_headers={"Authorization"='
        '"AEGIS_MCP_NOTES_AUTHORIZATION"}'
    ) in out
    assert 'mcp_servers.notes.default_tools_approval_mode="approve"' in out
    (exclude,) = [a for a in out if a.startswith("shell_environment_policy.exclude=")]
    assert "PHONE_STATE" in exclude and "AEGIS_MCP_NOTES_AUTHORIZATION" in exclude
    env = child_env({}, ("http://a/mcp", "tok"), servers=BOTH)
    assert env["PHONE_STATE"] == "SECRET-2"
    assert env["AEGIS_MCP_NOTES_AUTHORIZATION"] == "Bearer SECRET-1"


def test_pi_agent_dirs_differ_by_server_set_and_hold_no_value(tmp_path):
    a = agent_dir(tmp_path, "http://a/mcp", tmp_path, servers=(NOTES,))
    b = agent_dir(tmp_path, "http://a/mcp", tmp_path, servers=BOTH)
    assert a != b and a == agent_dir(
        tmp_path, "http://a/mcp", tmp_path, servers=(NOTES,)
    )
    assert "SECRET" not in (b / "mcp.json").read_text()
    held = json.loads((b / "mcp.json").read_text())["mcpServers"]
    assert held["phone"]["env"] == {"PHONE_STATE": "${AEGIS_MCP_PHONE_PHONE_STATE}"}
    assert agent_dir(tmp_path, "http://a/mcp", tmp_path).name == "agent"


def test_pi_argv_lets_a_read_session_call_the_servers_tools(tmp_path):
    cmd = pi.argv("pi", launch(tmp_path, permission="read"), None, None)
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "mcp__aegis__*" in tools and "mcp__phone__*" in tools
    assert "--no-mcp" not in cmd


def test_cursor_gets_both_servers_with_values_as_pairs():
    out = mcp_servers(("http://a/mcp", "tok"), BOTH)
    assert [s["name"] for s in out] == ["aegis", "notes", "phone"]
    assert out[1] == {
        "name": "notes",
        "type": "http",
        "url": "http://n/mcp",
        "headers": [{"name": "Authorization", "value": "Bearer SECRET-1"}],
    }
    assert out[2] == {
        "name": "phone",
        "command": "uv",
        "args": ["run", "phone"],
        "env": [{"name": "PHONE_STATE", "value": "SECRET-2"}],
    }


def test_no_servers_leaves_every_driver_as_it_was(tmp_path):
    assert list(json.loads(mcp_config("http://a/mcp"))["mcpServers"]) == ["aegis"]
    assert list(child_config(("u", "t"), "full")["mcp"]["servers"]) == ["aegis"]
    assert not any(a.startswith("mcp_servers.notes") for a in argv("codex", "u"))
    assert len(mcp_servers(("u", "t"))) == 1
    assert agent_dir(tmp_path, "u", tmp_path).name == "agent"


# -- each process class hands them to its child ----------------------------------

FAKE_PI = Path(__file__).parent / "fake_pi.py"


@pytest.fixture
def bins(tmp_path, fake_claude, fake_opencode, fake_codex, fake_cursor) -> dict:
    pi_bin = tmp_path / "bin-pi" / "pi"
    pi_bin.parent.mkdir()
    pi_bin.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE_PI} "$@"\n')
    pi_bin.chmod(0o755)
    return {
        "claude-code": fake_claude,
        "opencode": fake_opencode,
        "codex": fake_codex,
        "cursor": fake_cursor,
        "pi": str(pi_bin),
    }


@pytest.mark.parametrize("name", ["claude-code", "opencode", "codex", "cursor", "pi"])
async def test_a_started_child_gets_the_servers_and_no_value_in_its_argv(
    name, tmp_path, bins, monkeypatch
):
    seen: list[tuple[list[str], dict]] = []
    real = aegis_launch.child

    async def child(argv, **kw):
        seen.append((list(argv), dict(kw.get("env") or {})))
        return await real(argv, **kw)

    monkeypatch.setattr(aegis_launch, "child", child)
    h = harness_for(
        name, bins["claude-code"], bins["opencode"], bins["codex"], bins["cursor"],
        bins["pi"],
    )  # fmt: skip
    model = {"opencode": "fake/m1", "pi": "fake/m1", "cursor": "fake-pro"}
    proc = h.process(replace(launch(tmp_path), model=model.get(name, "opus")))
    await proc.start()
    try:
        argv, env = next((a, e) for a, e in seen if "--version" not in a)
        assert "SECRET" not in " ".join(argv)
        if name == "claude-code":
            cfg = json.loads(argv[argv.index("--mcp-config") + 1])["mcpServers"]
            assert {"notes", "phone"} <= set(cfg)
            assert env["AEGIS_MCP_NOTES_AUTHORIZATION"] == "Bearer SECRET-1"
        elif name == "opencode":
            cfg = json.loads(env["OPENCODE_CONFIG_CONTENT"])["mcp"]["servers"]
            assert {"notes", "phone"} <= set(cfg)
        elif name == "codex":
            assert 'mcp_servers.phone.env_vars=["PHONE_STATE"]' in argv
            assert env["PHONE_STATE"] == "SECRET-2"
        elif name == "pi":
            d = Path(env["PI_CODING_AGENT_DIR"])
            assert d.name.startswith("agent-")
            assert "SECRET" not in (d / "mcp.json").read_text()
            assert env["AEGIS_MCP_PHONE_PHONE_STATE"] == "SECRET-2"
        else:
            log = tmp_path / "fake-cursor.log"
            await until(lambda: "mcp " in log.read_text(), what="session/new")
            assert "mcp aegis notes phone" in log.read_text()
    finally:
        await proc.terminate()
    # What aegis wrote; a fake's home stands for the harness's own state.
    held = [
        p.relative_to(tmp_path)
        for p in tmp_path.rglob("*")
        if p.is_file()
        and not p.relative_to(tmp_path).parts[0].startswith("fake-")
        and b"SECRET" in p.read_bytes()
    ]
    assert held == []
