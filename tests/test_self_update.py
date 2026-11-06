"""self_update.py: which installer owns the install, and an update's steps up
to the restart (#955)."""

import asyncio
import sys
from pathlib import Path

import pytest

from aegis import self_update
from aegis.self_update import Facts, Install, Updater, detect

HOME = Path("/home/ana")
UV_TOOLS = HOME / ".local/share/uv/tools"
UV_CACHE = HOME / ".cache/uv"
PIPX = HOME / ".local/share/pipx/venvs"


def facts(prefix: Path, **kw) -> Facts:
    base = dict(
        prefix=prefix,
        base_prefix=Path("/usr"),
        executable=str(prefix / "bin/python"),
        dev=False,
        installer=None,
        uv_tools=UV_TOOLS,
        uv_cache=UV_CACHE,
        pipx_venvs=PIPX,
    )
    return Facts(**(base | kw))


@pytest.mark.parametrize(
    ("f", "by", "argv"),
    [
        (facts(UV_TOOLS / "aegis-harness", dev=True), "a build from source", None),
        (facts(UV_CACHE / "archive-v0/abc"), "uvx", None),
        (
            facts(UV_TOOLS / "aegis-harness", installer="uv"),
            "uv tool",
            ["uv", "tool", "upgrade", "aegis-harness"],
        ),
        (
            facts(PIPX / "aegis-harness", installer="pip"),
            "pipx",
            ["pipx", "upgrade", "aegis-harness"],
        ),
        (
            facts(HOME / "srv/.venv", installer="uv"),
            "uv in a venv",
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(HOME / "srv/.venv/bin/python"),
                "-U",
                "aegis-harness",
            ],  # fmt: skip
        ),
        (
            facts(HOME / "srv/.venv", installer="pip"),
            "pip in a venv",
            [
                str(HOME / "srv/.venv/bin/python"),
                "-m",
                "pip",
                "install",
                "-U",
                "aegis-harness",
            ],  # fmt: skip
        ),
        (
            facts(Path("/usr"), installer="pip"),
            "an installer aegis does not know",
            None,
        ),
        (
            facts(HOME / "conda/envs/x", installer="conda"),
            "an installer aegis does not know",
            None,
        ),
    ],
)
def test_each_install_gets_its_own_installer_or_none(f, by, argv):
    got = detect(f)
    assert (got.by, got.argv) == (by, argv)
    assert got.command or f.dev  # a release always comes with a command


def test_a_missing_uv_or_pipx_reads_as_neither(tmp_path):
    f = facts(UV_TOOLS / "aegis-harness", installer="uv", uv_tools=None)
    assert detect(f).by == "uv in a venv"


def script(tmp_path: Path, name: str, body: str) -> list[str]:
    p = tmp_path / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(0o755)
    return [str(p)]


class Run:
    def __init__(self, tmp_path, install: str, version: str, running="1.0.0"):
        self.sent: list[dict] = []
        self.stopped = 0

        def stop():
            self.stopped += 1

        self.u = Updater(
            running,
            lambda ch, ops: self.sent.append(ops[0]["set"] | {"ch": ch}),
            Install("uv tool", "x", script(tmp_path, "install", install)),
            script(tmp_path, "aegis", f'echo "aegis {version}"'),
            stop,
        )

    def phases(self) -> list[str]:
        out: list[str] = []
        for s in self.sent:
            if not out or out[-1] != s["phase"]:
                out.append(s["phase"])
        return out


@pytest.fixture(autouse=True)
def no_pending(monkeypatch):
    monkeypatch.setattr(self_update, "_pending", None)
    monkeypatch.delenv("AEGIS_UPDATE_COMMAND", raising=False)


def test_an_update_installs_checks_and_restarts_on_the_same_arguments(tmp_path):
    r = Run(tmp_path, "echo Updated aegis-harness", "1.1.0")
    asyncio.run(r.u.run())
    assert r.phases() == ["installing", "checking", "restarting"]
    assert r.stopped == 1
    assert {s["ch"] for s in r.sent} == {"update"}
    assert "Updated aegis-harness" in r.u.lines
    assert r.u.version == "1.1.0"
    assert self_update.pending() == [*r.u._program, *sys.argv[1:]]


def test_a_failed_install_keeps_the_server_running(tmp_path):
    r = Run(tmp_path, "echo no network >&2; exit 2", "1.1.0")
    asyncio.run(r.u.run())
    assert r.phases() == ["installing", "failed"]
    assert r.u.error == "the install exited with 2"
    assert r.u.failed_at == "installing"
    assert "no network" in r.u.lines
    assert (r.stopped, self_update.pending()) == (0, None)


def test_an_install_that_did_not_move_the_version_does_not_restart(tmp_path):
    r = Run(tmp_path, "true", "1.0.0")
    asyncio.run(r.u.run())
    assert r.phases() == ["installing", "checking", "failed"]
    assert "pinned" in (r.u.error or "")
    assert r.u.failed_at == "checking"
    assert (r.stopped, self_update.pending()) == (0, None)


def test_a_new_build_that_does_not_start_does_not_restart(tmp_path):
    r = Run(tmp_path, "true", "1.1.0")
    r.u._program = script(tmp_path, "broken", "exit 1")
    asyncio.run(r.u.run())
    assert r.u.error == "the new build did not answer --version"
    assert r.stopped == 0


def test_a_second_update_while_one_runs_does_nothing(tmp_path):
    r = Run(tmp_path, "sleep 0.3", "1.1.0")

    async def both():
        await asyncio.gather(r.u.run(), r.u.run())

    asyncio.run(both())
    assert r.stopped == 1
    assert r.phases().count("installing") == 1


def test_an_install_aegis_cannot_update_fails_without_running_anything(tmp_path):
    u = Updater("1.0.0", lambda ch, ops: None, Install("uvx", "uvx aegis-harness"))
    asyncio.run(u.run())
    assert (u.phase, u.lines) == ("failed", [])
