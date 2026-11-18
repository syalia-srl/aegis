"""Every process aegis starts, started here (#548).

No other module starts a process, by ``subprocess``, asyncio's spawns, the
``os`` and ``pty`` process calls, ``webbrowser`` or ``multiprocessing``, whether
it calls them or hands them to an executor; ``tests/test_launch_only.py`` fails
if one does. One place starts them all, so a test can watch every start and a rule
for every process (a cgroup, an exit) applies to each.

A caller picks one of six shapes and keeps its own reading and timeout
logic. Process signals use the shared helper below:

- ``child``: async, with pipes, optionally in a session's cgroup with the OOM
  score raised. Harnesses, monitors, workflow steps, one-shot runs, probes.
- ``pty``: async, on a terminal that becomes the child's controlling terminal.
- ``run``: sync, output captured, with a timeout. git, gh, systemctl.
- ``detach``: sync, in a session of its own, never waited for.
- ``browser``: a URL handed to the desktop's browser through ``webbrowser``,
  which starts its own process.
- ``replace``: the server's own process replaced by ``argv``, same pid, once
  it has stopped: the restart after an update (self_update.py).

``child`` and ``pty`` also take ``via``, the linked server the process reaches
the internet through (exit.py), or None for this machine's own route. It is
required: a process that should exit and forgets to say would
leave from the address the person meant to avoid. ``resolve_via`` gives what
the configuration says. ``run`` has no ``via``: it is the server's housekeeping,
``git`` and ``systemctl``, on local state.

``asyncio.create_subprocess_exec`` and ``subprocess.run``/``Popen`` are looked
up on their modules at each call, so a test that patches them reaches every
process.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .cgroups import wrap
from .harness import raise_oom_score

PIPE = asyncio.subprocess.PIPE
DEVNULL = asyncio.subprocess.DEVNULL
STDOUT = asyncio.subprocess.STDOUT
# asyncio.create_subprocess_exec's own default for a pipe's line limit.
LIMIT = 2**16
# die_with_parent: go on only while the parent is the server that started it.
_STILL_PARENT = '[ "$PPID" = "$0" ] || exit 143; exec "$@"'

# The server's exits (exit.py) and how to read its configuration; None off.
_exits: Any = None
_resolve: Callable[[str | None], str | None] | None = None


def configure_exits(
    exits: Any, resolve: Callable[[str | None], str | None] | None
) -> None:
    global _exits, _resolve
    _exits, _resolve = exits, resolve


def exits_in_force() -> Any:
    return _exits


def resolve_via(agent: str | None = None) -> str | None:
    """The link a process started now exits through: the one ``agent`` names,
    or the server's, or None."""
    return None if _resolve is None else _resolve(agent)


def _via(via: str | None, env: dict[str, str] | None) -> dict[str, str] | None:
    if via is None or _exits is None:
        return env
    base = os.environ if env is None else env
    # Git's own choice of ssh stays: it may carry a key the person needs. A
    # person who set one chose it, and it goes direct like a bare ``ssh``.
    keep = (
        {"GIT_SSH_COMMAND": base["GIT_SSH_COMMAND"]}
        if "GIT_SSH_COMMAND" in base
        else {}
    )
    return {**base, **_exits.env(via), **keep}


def exit_proxy(via: str | None) -> dict[str, str] | None:
    """The exit for a program that takes its proxy as a setting: Chromium."""
    return None if via is None or _exits is None else _exits.get(via).playwright_proxy()


async def child(
    argv: list[str],
    *,
    via: str | None,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    stdin=None,
    stdout=None,
    stderr=None,
    cgroup: Path | None = None,
    oom: bool = False,
    new_session: bool = False,
    die_with_parent: bool = False,
    limit: int = LIMIT,
) -> asyncio.subprocess.Process:
    """``argv`` started inside ``cgroup`` when there is one. ``env`` None is
    the server's environment. Raises FileNotFoundError for a missing binary,
    with or without the cgroup.

    ``die_with_parent`` has the kernel send the child SIGTERM when the thread
    that started it ends, through ``setpriv --pdeathsig`` (a preexec_fn is
    unsafe in a threaded server): for a child that is no use to anyone once
    its parent is gone, such as an OpenCode server (#620). A parent that ended
    before setpriv set the signal sends none, so a shell checks the parent
    after it and then execs the program, whose pid stays the child's. Without
    setpriv on PATH the child starts as it would without the flag."""
    setpriv = shutil.which("setpriv") if die_with_parent else None
    if setpriv is not None:
        exe = shutil.which(argv[0])
        if exe is None:
            raise FileNotFoundError(argv[0])
        argv = [setpriv, "--pdeathsig", "TERM", "/bin/sh", "-c", _STILL_PARENT,
                str(os.getpid()), exe, *argv[1:]]  # fmt: skip
    env = _via(via, env)
    proc = await asyncio.create_subprocess_exec(
        *wrap(cgroup, argv),
        cwd=cwd,
        env=env,
        stdin=stdin,
        stdout=stdout,
        stderr=stderr,
        start_new_session=new_session,
        limit=limit,
    )
    if oom:
        raise_oom_score(proc.pid)
    return proc


async def pty(
    argv: list[str],
    *,
    via: str | None,
    tty: int,
    cwd: str | Path,
    env: dict[str, str] | None,
    cgroup: Path | None = None,
) -> asyncio.subprocess.Process:
    """``argv`` on the terminal ``tty``, as its controlling terminal
    (``setsid --ctty``: a preexec_fn is unsafe in a threaded server), in
    ``cgroup`` when there is one, with the OOM score raised."""
    proc = await asyncio.create_subprocess_exec(
        *wrap(cgroup, ["setsid", "--ctty", *argv]),
        stdin=tty,
        stdout=tty,
        stderr=tty,
        cwd=cwd,
        env=_via(via, env),
    )
    raise_oom_score(proc.pid)
    return proc


def run(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    env: dict[str, str] | None = None,
    input: bytes | str | None = None,
    timeout: float | None = None,
    text: bool = False,
    errors: str | None = None,
) -> subprocess.CompletedProcess:
    """``argv`` run to its end with its output captured. OSError and
    subprocess.TimeoutExpired reach the caller, as from subprocess.run."""
    return subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        input=input,
        capture_output=True,
        timeout=timeout,
        text=text,
        errors=errors,
    )


def signal_process(pid: int, sig: int, *, group: bool = False) -> None:
    """Signal a child or a process group. ``pid`` must be positive; a process
    already gone is stopped."""
    if pid <= 0:
        raise ValueError("pid must be positive")
    try:
        (os.killpg if group else os.kill)(pid, sig)
    except ProcessLookupError:
        pass


async def signal_process_async(pid: int, sig: int, *, group: bool = False) -> None:
    """``signal_process`` for a caller on the event loop."""
    signal_process(pid, sig, group=group)


def detach(
    argv: list[str],
    *,
    cwd: str | Path | None = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
) -> subprocess.Popen:
    """``argv`` in a session of its own, with no stdin, not waited for."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
        cwd=cwd,
    )


def browser(url: str) -> None:
    """``url`` opened by ``webbrowser``, which picks the desktop's browser and
    starts it itself."""
    webbrowser.open(url)


def replace(argv: list[str]) -> None:
    """The server's process becomes ``argv``: same pid, cgroup and unit, so a
    supervisor sees nothing. Only after a clean stop: nothing runs after it."""
    os.execv(argv[0], argv)
