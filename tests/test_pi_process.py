"""PiProcess against tests/fake_pi.py: the commands it sends and the lines it
hands the session."""

import asyncio
import json
import sys
from pathlib import Path

import pytest

from aegis.claude.process import ControlError
from aegis.harness import Launch
from aegis.pi.process import PiProcess

FAKE = Path(__file__).parent / "fake_pi.py"


def make(
    tmp_path: Path, monkeypatch, permission: str = "full", resume: str | None = None
):
    bin = tmp_path / "pi"
    bin.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} "$@"\n')
    bin.chmod(0o755)
    monkeypatch.setenv("FAKE_PI_ARGV", str(tmp_path / "argv.json"))
    lines: list[dict] = []
    exits: list[int] = []
    launch = Launch(
        cwd=tmp_path, model="fake/m1", effort="low", permission=permission,
        resume_id=resume, mcp=("http://127.0.0.1:1/mcp", "tok"), system_prompt="be brief",
        stderr_path=tmp_path / "state" / "stderr" / "s1.log",
        on_line=lambda line: lines.append(json.loads(line)),
        on_exit=lambda code, tail: exits.append(code), log_id="s1",
    )  # fmt: skip
    return PiProcess(str(bin), launch), lines, exits


async def until(pred, timeout: float = 5.0):
    for _ in range(int(timeout / 0.02)):
        if pred():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("condition never held")


def methods(lines):
    return [m.get("method") or m.get("type") for m in lines]


async def test_a_turn_ends_with_its_cost(tmp_path, monkeypatch):
    p, lines, _ = make(tmp_path, monkeypatch)
    await p.start()
    try:
        assert lines[0]["params"]["session_id"] == "/tmp/fake-pi/s.jsonl"
        await p.send("hello")
        await until(lambda: "aegis/turn_done" in methods(lines))
        done = lines[-1]["params"]
        assert done["stop_reason"] == "stop" and done["cost_usd"] == 0.25
        assert done["context_window"] == 1000
        assert methods(lines) == [
            "aegis/session", "aegis/echo", "message_update", "message_end", "aegis/turn_done",
        ]  # fmt: skip
    finally:
        await p.terminate()


async def test_the_launch_carries_model_effort_tools_and_agent_dir(
    tmp_path, monkeypatch
):
    p, _, _ = make(tmp_path, monkeypatch, permission="read")
    await p.start()
    await p.terminate()
    seen = json.loads((tmp_path / "argv.json").read_text())
    argv = seen["argv"]
    assert argv[:2] == ["--mode", "rpc"]
    assert argv[argv.index("--model") + 1] == "fake/m1"
    assert argv[argv.index("--thinking") + 1] == "low"
    assert argv[argv.index("--append-system-prompt") + 1] == "be brief"
    assert argv[argv.index("--tools") + 1].endswith("tool_search,mcp__aegis__*")
    assert "bash" not in argv[argv.index("--tools") + 1]
    mcp = json.loads((Path(seen["agent_dir"]) / "mcp.json").read_text())
    server = mcp["mcpServers"]["aegis"]
    assert server["headers"] == {"X-Aegis-Session": "${AEGIS_SESSION_TOKEN}"}
    assert server["url"] == "http://127.0.0.1:1/mcp"
    assert "tok" not in json.dumps(mcp)  # the token stays in the environment


async def test_a_resume_passes_the_session_file(tmp_path, monkeypatch):
    p, _, _ = make(tmp_path, monkeypatch, resume="/tmp/fake-pi/old.jsonl")
    await p.start()
    await p.terminate()
    argv = json.loads((tmp_path / "argv.json").read_text())["argv"]
    assert argv[argv.index("--session") + 1] == "/tmp/fake-pi/old.jsonl"


async def test_a_message_mid_turn_is_a_steer_and_an_interrupt_aborts(
    tmp_path, monkeypatch
):
    p, lines, _ = make(tmp_path, monkeypatch)
    await p.start()
    try:
        await p.send("slow job")
        await p.send("also this")
        assert methods(lines).count("aegis/echo") == 2
        await p.interrupt()
        await until(lambda: "aegis/turn_done" in methods(lines))
        assert lines[-1]["params"]["stop_reason"] == "interrupted"
        await p.send("again")  # idle again: a new prompt, not a steer
        await until(lambda: methods(lines).count("aegis/turn_done") == 2)
        assert lines[-2]["type"] == "message_end"
        assert "also this" in lines[-2]["message"]["content"][0]["text"]
    finally:
        await p.terminate()


async def test_settings_and_catalog(tmp_path, monkeypatch):
    p, lines, _ = make(tmp_path, monkeypatch)
    await p.start()
    try:
        await p.set("model", "fake/m2")
        assert lines[-1] == {"method": "aegis/turn", "params": {"model": "fake/m2"}}
        await p.set("effort", "high")
        with pytest.raises(ControlError):
            await p.set("effort", "turbo")
        with pytest.raises(ControlError):
            await p.set("model", "nope/x")
        cat = await p.catalog()
        assert [m.value for m in cat.models] == ["fake/m1", "fake/m2"]
        assert cat.model("fake/m1").efforts and not cat.model("fake/m2").efforts
        await p.set("permission", "write")  # restarts the child
        assert p.running and p.permission == "write"
    finally:
        await p.terminate()
