"""One ``pi --mode rpc`` child per session, driven over JSONL on stdio.

Measured on pi 1.5.0 (spec ``2026-11-05-pi-harness-design.html``; fixture
``tests/fixtures/pi/rpc-turn.jsonl``):

- A command is ``{id, type, ...}``; its answer is ``{type: "response", id,
  command, success, data | error}``. ``prompt`` answers at once and the turn
  runs until ``agent_end``.
- A message sent mid-turn goes as ``steer``: pi reads it after the running
  tool call, inside the same turn (measured: the steered word reached the
  final answer).
- ``abort`` stops the turn and kills a running bash command (measured:
  ``Command aborted``); the turn then ends with an assistant message whose
  ``stopReason`` is ``error`` (``This operation was aborted``).
- ``get_session_stats`` answers the session's cumulative ``cost`` and its
  ``contextUsage.contextWindow``, which ``aegis/turn_done`` carries to the card.
- The session file is pi's id for the conversation: ``--session <file>``
  resumes it, from the same cwd (from another, pi asks whether to fork).
- The allowlist of tools is a start flag, so a permission change restarts the
  child on the same session file, between turns only.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
import signal
import time
from collections import deque
from pathlib import Path
from typing import Any

from .. import launch
from ..claude.control import Catalog
from ..claude.process import ControlError
from ..harness import Launch, session_env
from .config import (
    AGENT_DIR_VAR,
    EFFORTS,
    agent_dir,
    argv,
    catalog_from,
    models_from,
    split_model,
    state_dir,
)

START_S = 60.0
REQUEST_S = 30.0
TERM_GRACE_S = 5.0
STDERR_TAIL = 64
# A whole file read or a long command output arrives on one line.
LINE_LIMIT = 64 * 1024 * 1024
EXIT_POLL_S = 0.1
# pi's events that carry nothing the transcript draws: never stored.
DROPPED = frozenset(
    {
        "agent_start",
        "agent_settled",
        "turn_start",
        "turn_end",
        "message_start",
        "queue_update",
    }
)


class RpcError(Exception):
    """pi answered a command with ``success: false``."""


class Exited(ConnectionResetError):
    """The child exited while a command waited for its answer."""


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


async def _exited(proc: asyncio.subprocess.Process) -> int:
    while proc.returncode is None:
        await asyncio.sleep(EXIT_POLL_S)
    return proc.returncode or 0


class PiProcess:
    def __init__(self, bin: str, launch: Launch) -> None:
        self._bin = bin
        self._launch = launch
        self.model, self.effort, self.permission = (
            launch.model,
            launch.effort,
            launch.permission,
        )
        self._root = state_dir(launch)
        # A probe (no log id) keeps no conversation.
        self._session_dir = (
            self._root / "sessions" / launch.log_id if launch.log_id else None
        )
        self._session: str | None = launch.resume_id
        self._window: int | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._next = 0
        self._pending: dict[str, asyncio.Future] = {}
        self._tasks: set[asyncio.Task] = set()
        self._wait_task: asyncio.Task | None = None
        self._tail: deque[str] = deque(maxlen=STDERR_TAIL)
        self._catalog: Catalog | None = None
        self._busy = False
        self._started = 0.0
        self._interrupted = False
        self._quiet = False

    # -- what the session reads --------------------------------------
    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc else None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    @property
    def session_id(self) -> str | None:
        return self._session

    # -- lifecycle ----------------------------------------------------
    async def start(self) -> None:
        await self._spawn()
        try:
            state = await self._call({"type": "get_state"}, START_S)
        except (RpcError, OSError, TimeoutError) as e:
            await self._end_child()
            tail = "; ".join(self._tail) or "no output"
            raise ConnectionError(f"pi did not start: {e}: {tail}") from e
        self._session = str(state.get("sessionFile") or "") or self._session
        model = _dict(state.get("model"))
        if isinstance(model.get("contextWindow"), int):
            self._window = model["contextWindow"]
        if not self.model and model.get("provider") and model.get("id"):
            self.model = f"{model['provider']}/{model['id']}"
        self._emit(
            "aegis/session",
            {
                "session_id": self._session,
                "model": self.model,
                "cwd": str(self._launch.cwd),
            },
        )

    async def _spawn(self) -> None:
        self._quiet = False
        self._launch.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        if self._session_dir is not None:
            self._session_dir.mkdir(parents=True, exist_ok=True)
        env = session_env(self._launch) or dict(os.environ)
        env[AGENT_DIR_VAR] = str(
            agent_dir(self._root, self._launch.mcp[0] if self._launch.mcp else None)
        )
        launch_now = dataclasses.replace(
            self._launch,
            model=self.model,
            effort=self.effort,
            permission=self.permission,
        )
        self._proc = await launch.child(
            argv(self._bin, launch_now, self._session_dir, self._session),
            cwd=self._launch.cwd,
            env=env,
            stdin=launch.PIPE,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            limit=LINE_LIMIT,
            new_session=True,
            cgroup=self._launch.cgroup,
            oom=True,
            user=self._launch.user,
            via=self._launch.via,
        )
        self._keep(self._drain(self._proc.stderr))
        self._keep(self._read())
        self._wait_task = asyncio.create_task(self._wait())

    def _keep(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    def _note(self, line: str) -> None:
        if not line:
            return
        self._tail.append(line)
        try:
            with self._launch.stderr_path.open("a") as f:
                f.write(line + "\n")
        except OSError:
            pass

    async def _drain(self, stream: asyncio.StreamReader | None) -> None:
        if stream is None:
            return
        while raw := await stream.readline():
            self._note(raw.decode(errors="replace").rstrip())

    async def _wait(self) -> None:
        assert self._proc is not None
        proc = self._proc
        code = await _exited(proc)
        await self._signal(proc, signal.SIGKILL)
        self._fail_pending(Exited("pi exited"))
        self._busy = False
        if self._quiet:
            return
        self._quiet = True
        await asyncio.sleep(0.05)  # let the stderr drain catch up
        self._launch.on_exit(code, list(self._tail))

    async def terminate(self) -> None:
        await self._end_child()

    async def _end_child(self) -> None:
        self._quiet = True
        proc = self._proc
        if proc is not None and proc.returncode is None:
            if proc.stdin is not None:
                proc.stdin.close()  # rpc mode ends when its stdin ends
            try:
                await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
            except TimeoutError:
                await self._signal(proc, signal.SIGTERM)
                try:
                    await asyncio.wait_for(_exited(proc), TERM_GRACE_S)
                except TimeoutError:
                    await self._signal(proc, signal.SIGKILL)
                    await _exited(proc)
        if proc is not None:
            await self._signal(proc, signal.SIGKILL)
        for t in list(self._tasks):
            t.cancel()
        if self._wait_task is not None:
            self._wait_task.cancel()
        self._fail_pending(ConnectionResetError("pi was stopped"))

    async def _signal(self, proc: asyncio.subprocess.Process, sig: int) -> None:
        await launch.signal_process_async(
            proc.pid, sig, user=self._launch.user, group=True
        )

    def _fail_pending(self, err: Exception) -> None:
        pending, self._pending = self._pending, {}
        for fut in pending.values():
            if not fut.done():
                fut.set_exception(err)

    # -- the wire -----------------------------------------------------
    async def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while raw := await stdout.readline():
            line = raw.decode(errors="replace").strip()
            if not line:
                continue
            try:
                msg: Any = json.loads(line)
            except ValueError:
                self._note(f"not JSON on stdout: {line[:200]}")
                continue
            if not isinstance(msg, dict):
                continue
            kind = msg.get("type")
            if kind == "response":
                fut = self._pending.pop(str(msg.get("id")), None)
                if fut is not None and not fut.done():
                    fut.set_result(msg)
            elif kind == "agent_end":
                self._keep(self._finish(msg))
            elif kind in DROPPED:
                continue
            elif (
                kind == "message_end"
                and _dict(msg.get("message")).get("role") != "assistant"
            ):
                continue  # the prompt and steers are aegis/echo lines
            elif kind == "extension_ui_request":
                self._extension_ui(msg)
            else:
                self._launch.on_line(line)

    def _extension_ui(self, msg: dict) -> None:
        """An extension's UI request. A notice goes to the stderr log; a
        dialog (select, confirm, input, editor) is cancelled, since a dialog
        nobody answers waits forever and aegis has no one to ask."""
        method = str(msg.get("method") or "")
        self._note(
            f"pi {method}: {msg.get('message') or msg.get('title') or ''}".rstrip()
        )
        if method in ("select", "confirm", "input", "editor"):
            self._write(
                {
                    "type": "extension_ui_response",
                    "id": msg.get("id"),
                    "cancelled": True,
                }
            )

    def _emit(self, method: str, params: dict) -> None:
        self._launch.on_line(json.dumps({"method": method, "params": params}))

    def _write(self, obj: dict) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise ConnectionResetError("pi is not running")
        try:
            self._proc.stdin.write(json.dumps(obj).encode() + b"\n")
        except (BrokenPipeError, ConnectionResetError, RuntimeError) as e:
            raise ConnectionResetError(str(e)) from e

    def _begin(self, cmd: dict) -> tuple[str, asyncio.Future]:
        """A command written to the child: its id and its future, registered
        before the write and dropped if the write fails."""
        if not self.running:
            raise ConnectionResetError("pi is not running")
        self._next += 1
        rid = f"a{self._next}"
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[rid] = fut
        try:
            self._write({"id": rid, **cmd})
        except BaseException:
            self._pending.pop(rid, None)
            raise
        return rid, fut

    async def _await(
        self, rid: str, fut: asyncio.Future, timeout: float | None
    ) -> dict:
        try:
            msg = await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(rid, None)
        if not msg.get("success"):
            raise RpcError(str(msg.get("error") or f"{msg.get('command')} failed"))
        return _dict(msg.get("data"))

    async def _call(self, cmd: dict, timeout: float | None = REQUEST_S) -> dict:
        rid, fut = self._begin(cmd)
        return await self._await(rid, fut, timeout)

    # -- turns ---------------------------------------------------------
    async def send(self, text: str) -> None:
        # The command is written before the echo, so a message that never
        # left is not drawn; the echo is drawn before pi's answer can be.
        if self._busy:
            rid, fut = self._begin({"type": "steer", "message": text})
            self._emit("aegis/echo", {"text": text})
            await self._await(rid, fut, REQUEST_S)
            return
        self._busy = True
        self._interrupted = False
        self._started = time.monotonic()
        try:
            rid, fut = self._begin({"type": "prompt", "message": text})
        except BaseException:
            self._busy = False
            raise
        self._emit("aegis/echo", {"text": text})
        try:
            await self._await(rid, fut, REQUEST_S)
        except RpcError as e:
            self._busy = False
            self._emit("aegis/turn_done", {"stop_reason": "error", "error": str(e)})

    async def _finish(self, end: dict) -> None:
        """``agent_end`` came: ask for the cost, then close the turn."""
        messages = end.get("messages")
        last = _dict(messages[-1]) if isinstance(messages, list) and messages else {}
        stop = (
            "interrupted"
            if self._interrupted
            else str(last.get("stopReason") or "stop")
        )
        params: dict[str, Any] = {
            "stop_reason": stop,
            "duration_ms": int((time.monotonic() - self._started) * 1000),
            "context_window": self._window,
        }
        if stop == "error" and last.get("errorMessage"):
            params["error"] = str(last["errorMessage"])
        try:
            stats = await self._call({"type": "get_session_stats"})
        except (RpcError, OSError, TimeoutError):
            stats = {}
        if isinstance(stats.get("cost"), (int, float)):
            params["cost_usd"] = stats["cost"]
        window = _dict(stats.get("contextUsage")).get("contextWindow")
        if isinstance(window, int):
            params["context_window"] = self._window = window
        self._busy = False
        self._emit("aegis/turn_done", params)

    async def interrupt(self) -> None:
        if not self._busy:
            return
        self._interrupted = True
        await self._call({"type": "abort"})

    # -- settings ------------------------------------------------------
    async def set(self, kind: str, value: str) -> None:
        if kind == "model":
            provider, model_id = split_model(value)
            try:
                await self._call(
                    {"type": "set_model", "provider": provider, "modelId": model_id}
                )
            except RpcError as e:
                raise ControlError(str(e)) from e
            self.model = value
            self._emit("aegis/turn", {"model": value})
        elif kind == "effort":
            if value not in EFFORTS:
                raise ControlError(f"pi's thinking levels are {', '.join(EFFORTS)}")
            try:
                await self._call({"type": "set_thinking_level", "level": value})
            except RpcError as e:
                raise ControlError(str(e)) from e
            self.effort = value
        elif kind == "permission":
            await self._set_permission(value)

    async def _set_permission(self, value: str) -> None:
        """pi's allowlist is a start flag: restart on the same session file."""
        if value == self.permission:
            return
        if self._busy:
            raise ControlError(
                "pi takes a permission at start; change it between turns"
            )
        before = self.permission
        self.permission = value
        try:
            await self._end_child()
            await self._spawn()
            await self._call({"type": "get_state"}, START_S)
        except BaseException:
            self.permission = before
            raise

    # -- catalog --------------------------------------------------------
    async def catalog(self) -> Catalog:
        if self._catalog is None:
            try:
                data = await self._call({"type": "get_available_models"})
            except (RpcError, TimeoutError) as e:
                raise ConnectionError(f"pi gave no catalog: {e}") from e
            models = data.get("models")
            self._catalog = catalog_from(
                models_from(models if isinstance(models, list) else [], self.model)
            )
        return self._catalog


async def probe(bin: str, cwd: Path, stderr_path: Path, model: str = "") -> Catalog:
    """A catalog for a cwd with no live process: start pi, ask its models,
    end it. No prompt is sent, so no tokens."""
    p = PiProcess(
        bin,
        Launch(
            cwd=cwd, model=model, effort="", permission="read", resume_id=None,
            mcp=None, system_prompt=None, stderr_path=stderr_path,
            on_line=lambda line: None, on_exit=lambda code, tail: None,
        ),
    )  # fmt: skip
    await p._spawn()
    try:
        return await p.catalog()
    finally:
        await p.terminate()
