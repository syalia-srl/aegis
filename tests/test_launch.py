"""The four ways aegis starts a process (launch.py), each against a real child."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from aegis import launch
from aegis.harness import OOM_SCORE_ADJ


async def test_child_runs_argv_and_returns_the_process(tmp_path):
    p = await launch.child(
        ["sh", "-c", "echo hi"], cwd=tmp_path, stdout=launch.PIPE, via=None
    )
    out, _ = await p.communicate()
    assert out == b"hi\n" and p.returncode == 0


async def test_child_without_env_inherits_the_servers(monkeypatch):
    monkeypatch.setenv("AEGIS_LAUNCH_PROBE", "x")
    p = await launch.child(
        ["sh", "-c", "echo $AEGIS_LAUNCH_PROBE"],
        stdout=launch.PIPE,
        via=None,
    )
    assert (await p.communicate())[0] == b"x\n"


async def test_child_with_a_missing_binary_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"], via=None)
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"], cgroup=tmp_path, via=None)


async def test_child_with_oom_raises_the_score():
    p = await launch.child(["sleep", "5"], oom=True, via=None)
    try:
        score = Path(f"/proc/{p.pid}/oom_score_adj").read_text().strip()
        assert score == str(OOM_SCORE_ADJ)
    finally:
        p.kill()
        await p.wait()


async def test_child_with_new_session_leads_its_own_session():
    p = await launch.child(["sleep", "5"], new_session=True, via=None)
    try:
        assert os.getsid(p.pid) == p.pid
    finally:
        p.kill()
        await p.wait()


async def test_child_with_die_with_parent_ends_when_its_parent_does(tmp_path):
    """The parent starts a child that would sleep a minute, prints its pid and
    exits at once without a word to it (#620)."""
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import asyncio, os\n"
        "from aegis import launch\n"
        "async def main():\n"
        "    p = await launch.child(\n"
        "        ['sleep', '60'], via=None, stdout=launch.DEVNULL, die_with_parent=True\n"
        "    )\n"
        "    print(p.pid, flush=True)\n"
        "    os._exit(0)\n"
        "asyncio.run(main())\n"
    )
    p = await launch.child([sys.executable, str(parent)], stdout=launch.PIPE, via=None)
    out, _ = await p.communicate()
    pid = int(out)
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    alive = _alive(pid)
    if alive:
        os.kill(pid, 9)
    assert not alive


async def test_child_with_die_with_parent_keeps_a_missing_binary_an_error():
    with pytest.raises(FileNotFoundError):
        await launch.child(["no-such-binary-aegis"], via=None, die_with_parent=True)


def _alive(pid: int) -> bool:
    """Running: an orphan's zombie, reaped by whoever adopted it, is not."""
    try:
        return "State:\tZ" not in Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return False


async def test_pty_gives_the_child_the_terminal(tmp_path):
    master, slave = os.openpty()
    try:
        p = await launch.pty(
            ["sh", "-c", "tty"], tty=slave, cwd=tmp_path, env=None, via=None
        )
    finally:
        os.close(slave)
    out = b""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            chunk = os.read(master, 1024)
        except OSError:  # EIO: the child closed its side
            break
        if not chunk:
            break
        out += chunk
    os.close(master)
    await p.wait()
    assert out.startswith(b"/dev/pts/")


def test_run_captures_and_feeds_input():
    r = launch.run(["cat"], input=b"abc")
    assert r.stdout == b"abc" and r.returncode == 0


def test_run_timeout_raises_timeout_expired():
    with pytest.raises(subprocess.TimeoutExpired):
        launch.run(["sleep", "5"], timeout=0.2)


def test_detach_starts_a_new_session_and_does_not_wait():
    p = launch.detach(["sleep", "5"])
    try:
        assert os.getsid(p.pid) == p.pid and p.poll() is None
    finally:
        p.kill()
        p.wait()


def test_launch_calls_asyncio_through_its_module(monkeypatch):
    """tests/test_terminal.py patches asyncio.create_subprocess_exec; the
    launcher must look it up on the module at each call."""
    seen = []
    real = asyncio.create_subprocess_exec

    def spy(*argv, **kw):
        seen.append(argv)
        return real(*argv, **kw)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)

    async def go():
        p = await launch.child(["true"], via=None)
        await p.wait()

    asyncio.run(go())
    assert seen == [("true",)]


@pytest.fixture
def seen(monkeypatch):
    """The argv and env every spawn is asked for, without starting anything."""

    class Calls(list):
        cwds: list

    calls: Calls = Calls()
    calls_cwd: list = []

    class Proc:
        pid = 4242
        returncode = 0

    async def fake_exec(*argv, env=None, **kw):
        calls.append((list(argv), env))
        calls_cwd.append(kw.get("cwd"))
        return Proc()

    def fake_run(argv, env=None, **kw):
        calls.append((list(argv), env))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(launch, "raise_oom_score", lambda pid: None)
    calls.cwds = calls_cwd  # type: ignore[attr-defined]
    return calls


async def test_a_call_without_via_is_refused():
    """Required like ``user``, so a new call site has to say whether it exits."""
    with pytest.raises(TypeError, match="via"):
        await launch.child(["true"])  # type: ignore[call-arg]
