"""Updating aegis from the browser (#955): which installer owns the running
install, the command that upgrades it, and the restart onto the new build.

The installer is read from the running process only: ``sys.prefix`` against
uv's tool and cache directories and pipx's venvs, then the ``INSTALLER`` file
of the dist-info for a plain venv. Upgrading through any other tool would leave
two copies and a stale entry point, so an install none of the rows recognise
(a dev build, a ``uvx`` run, a system Python) gets no button, only the command.

An update installs, then asks the new entry point for ``--version``; only a
new number goes on to restart, so a broken or pinned install is caught while
the old server still runs. The restart is the clean stop a ``systemctl stop``
takes (uvicorn's exit flag, ``server.json`` written by restarts.py), then
``cli.serve`` replaces the process with the same entry point and arguments:
same pid, so a unit sees nothing and its ``Restart=`` does not matter.

``AEGIS_UPDATE_COMMAND`` replaces the installer's command, which keeps the
tests off the network. Spec: docs/superpowers/specs/2026-11-06-self-update-design.html
"""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from . import launch
from .version import DIST

INSTALL_TIMEOUT_S = 300.0
CHECK_TIMEOUT_S = 30.0
PROBE_TIMEOUT_S = 5.0
KEEP_LINES = 40

# Set once an update has passed its check: cli.serve replaces the process with
# it when uvicorn returns.
_pending: list[str] | None = None


@dataclass(frozen=True)
class Install:
    """Where the running aegis came from. ``argv`` is None when aegis cannot
    upgrade it itself; ``command`` is then what a person runs instead."""

    by: str
    command: str | None
    argv: list[str] | None = None

    def wire(self) -> dict:
        return {"by": self.by, "command": self.command, "can": self.argv is not None}


@dataclass(frozen=True)
class Facts:
    """What detection reads; ``probe`` fills it from this process."""

    prefix: Path
    base_prefix: Path
    executable: str
    dev: bool
    installer: str | None  # the dist-info's INSTALLER, stripped
    uv_tools: Path | None
    uv_cache: Path | None
    pipx_venvs: Path | None


def _under(path: Path, root: Path | None) -> bool:
    return root is not None and path.is_relative_to(root)


def detect(f: Facts) -> Install:
    """The first row of the spec's section 04 that matches."""
    if f.dev:
        return Install("a build from source", None)
    if _under(f.prefix, f.uv_cache):
        return Install("uvx", f"uvx {DIST}@latest")
    if f.uv_tools is not None and f.prefix.parent == f.uv_tools:
        argv = ["uv", "tool", "upgrade", DIST]
        return Install("uv tool", shlex.join(argv), argv)
    if f.pipx_venvs is not None and f.prefix.parent == f.pipx_venvs:
        argv = ["pipx", "upgrade", DIST]
        return Install("pipx", shlex.join(argv), argv)
    if f.prefix != f.base_prefix:
        if f.installer == "uv":
            argv = ["uv", "pip", "install", "--python", f.executable, "-U", DIST]
            return Install("uv in a venv", shlex.join(argv), argv)
        if f.installer == "pip":
            argv = [f.executable, "-m", "pip", "install", "-U", DIST]
            return Install("pip in a venv", shlex.join(argv), argv)
    return Install("an installer aegis does not know", f"pip install -U {DIST}")


def _ask(argv: list[str]) -> Path | None:
    try:
        out = launch.run(argv, user=None, text=True, timeout=PROBE_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = out.stdout.strip()
    return Path(line) if out.returncode == 0 and line else None


def probe() -> Facts:
    """This process's facts. Runs ``uv`` and ``pipx`` to ask for their
    directories, so call it off the event loop."""
    from .version import running

    try:
        installer = (distribution(DIST).read_text("INSTALLER") or "").strip() or None
    except PackageNotFoundError:
        installer = None
    return Facts(
        prefix=Path(sys.prefix),
        base_prefix=Path(sys.base_prefix),
        executable=sys.executable,
        dev=running()["dev"],
        installer=installer,
        uv_tools=_ask(["uv", "tool", "dir"]),
        uv_cache=_ask(["uv", "cache", "dir"]),
        pipx_venvs=_ask(["pipx", "environment", "--value", "PIPX_LOCAL_VENVS"]),
    )


def program() -> list[str]:
    """How this server was started, without its arguments, to start the new
    build the same way: the console script, or ``python -m aegis``."""
    argv0 = sys.argv[0]
    if argv0.endswith(".py") or not os.access(argv0, os.X_OK):
        return [sys.executable, "-m", "aegis"]
    return [os.path.abspath(argv0)]


def pending() -> list[str] | None:
    """The argv cli.serve replaces the process with, once uvicorn returns."""
    return _pending


# How to stop the server cleanly, set by cli.serve: uvicorn's should_exit, the
# flag its SIGTERM handler sets. A real SIGTERM would do the same stop, but
# uvicorn raises it again once it has stopped, which kills the process before
# it can replace itself.
_stopper: Callable[[], None] | None = None


def on_stop(fn: Callable[[], None]) -> None:
    global _stopper
    _stopper = fn


def _stop() -> None:
    if _stopper is not None:
        _stopper()
    else:
        signal.raise_signal(signal.SIGTERM)


class Updater:
    """One update at a time. ``wire`` is the ``update`` channel's state:
    ``phase`` is idle, installing, checking, restarting or failed."""

    def __init__(
        self,
        running: str | None,
        publish: Callable[[str, list[dict]], None],
        install: Install | None = None,
        program: list[str] | None = None,
        stop: Callable[[], None] = _stop,
    ) -> None:
        self.running = running
        self._publish = publish
        self._install = install
        self._program = program
        self._stop = stop
        self.phase = "idle"
        self.lines: list[str] = []
        self.error: str | None = None
        self.version: str | None = None
        self.failed_at: str | None = None  # the step a failure ended
        self._lock = asyncio.Lock()

    async def install(self) -> Install:
        if self._install is None:
            self._install = await asyncio.to_thread(lambda: detect(probe()))
        return self._install

    def wire(self) -> dict:
        return {
            "phase": self.phase,
            "lines": self.lines[-KEEP_LINES:],
            "error": self.error,
            "version": self.version,
            "failed_at": self.failed_at,
        }

    def _set(self, phase: str, error: str | None = None) -> None:
        self.failed_at = self.phase if phase == "failed" and self.busy() else None
        self.phase, self.error = phase, error
        self._publish("update", [{"set": self.wire()}])

    def _line(self, text: str) -> None:
        self.lines.append(text)
        self._publish("update", [{"set": self.wire()}])

    def busy(self) -> bool:
        return self.phase in ("installing", "checking", "restarting")

    async def run(self) -> None:
        """Install, check, restart. Refused while one runs; a failure leaves
        the server running and says why on the channel."""
        if self._lock.locked():
            return
        async with self._lock:
            inst = await self.install()
            argv = inst.argv
            if cmd := os.environ.get("AEGIS_UPDATE_COMMAND"):
                argv = shlex.split(cmd)
            if argv is None:
                self._set("failed", f"aegis cannot update an install by {inst.by}")
                return
            self.lines, self.version = [], None
            self._set("installing")
            self._line("$ " + shlex.join(argv))
            if (code := await self._stream(argv, INSTALL_TIMEOUT_S)) != 0:
                self._set("failed", f"the install exited with {code}")
                return
            self._set("checking")
            prog = self._program or program()
            new = await self._version(prog)
            if new is None:
                self._set("failed", "the new build did not answer --version")
                return
            self.version = new
            if new == self.running:
                self._set(
                    "failed",
                    f"the install left aegis at {new}: it is pinned, or no newer "
                    "release reached this installer",
                )
                return
            global _pending
            _pending = [*prog, *sys.argv[1:]]
            self._set("restarting")
            self._stop()

    async def _stream(self, argv: list[str], timeout: float) -> int | str:
        try:
            proc = await launch.child(
                argv, user=None, via=None, stdout=launch.PIPE, stderr=launch.STDOUT
            )
        except OSError as e:
            self._line(str(e))
            return "an error"
        assert proc.stdout is not None
        out = proc.stdout

        async def read() -> None:
            while line := await out.readline():
                self._line(line.decode(errors="replace").rstrip())

        try:
            await asyncio.wait_for(read(), timeout)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return f"a timeout after {int(timeout)} s"
        return await proc.wait()

    async def _version(self, prog: list[str]) -> str | None:
        try:
            proc = await launch.child(
                [*prog, "--version"],
                user=None,
                via=None,
                stdout=launch.PIPE,
                stderr=launch.STDOUT,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), CHECK_TIMEOUT_S)
        except (OSError, TimeoutError):
            return None
        words = out.decode(errors="replace").split()
        if proc.returncode != 0 or len(words) < 2 or words[0] != "aegis":
            return None
        self._line(f"new build: aegis {words[1]}")
        return words[1]
