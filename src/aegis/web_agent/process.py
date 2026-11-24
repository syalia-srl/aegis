"""A browser-backed Process with real, session-scoped Bash children.

The driver itself is an asyncio task. A response ending is not an Aegis turn
ending when it requests a tool: the real result must reach the provider first.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import signal
import time

from .. import launch
from ..call_auth import AUTH_VAR, CallAuth
from ..claude.control import Catalog, Model
from ..claude.process import ControlError
from ..harness import Launch, TOKEN_VAR, URL_VAR, session_env
from ..chrome import CallError
from .ledger import Ledger
from .protocol import ProtocolError, request
from .providers import Provider

POLL_S = 1.0
COMMAND_TIMEOUT_S = 120.0
OUTPUT_BYTES = 64 * 1024
OUTPUT_DRAIN_S = 1.0
TRANSIENT = frozenset(
    {"chrome_unavailable", "person_has_control", "scripts_off", "intervention_required"}
)

BRIEF = """You are an Aegis agent connected through a browser bridge.
Use your provider's own research capabilities when appropriate.
For local execution, return exactly one top-level fenced code block labeled bash.
Its first nonblank line must be exactly: # aegis: execute
Start the command with a descriptive comment and wait for the actual result.
Other code blocks are examples. Do not simulate tool output or execution.
Discover a tool with `aegis tools TOOL`; invoke it with `aegis call TOOL key=value`.
Arguments follow normal Bash quoting. Keep credentials out of commands and output.
Use Aegis tools for plans, delegation, files and attention state.
When a command is refused, adapt to the refusal; do not bypass it.
Working directory: {cwd}
Permission: {permission}
For this initial brief, acknowledge with AEGIS_READY and wait for the task.
Do not request any command in your acknowledgement.
"""


class RecoveryError(RuntimeError):
    """A browser or command outcome cannot safely be replayed."""


class WebProcess:
    def __init__(self, context: Launch, provider: Provider) -> None:
        self.context, self.provider = context, provider
        self.permission = context.permission
        self._root = (
            context.stderr_path.parent.parent / "web" / (context.log_id or "probe")
        )
        self.ledger = Ledger(self._root / "ledger.json")
        self._id = context.resume_id or f"{provider.name}:{context.log_id}"
        self._active = False
        self._connected = False
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._runner: asyncio.Task | None = None
        self._turn: asyncio.Task | None = None
        self._child: asyncio.subprocess.Process | None = None
        self._pgid: int | None = None
        self._auth: CallAuth | None = None
        self._state_text: tuple[str, str] | None = None

    @property
    def pid(self) -> int | None:
        return self._pgid

    @property
    def running(self) -> bool:
        return self._active

    @property
    def session_id(self) -> str:
        return self._id

    def _emit(self, kind: str, **values: object) -> None:
        self.context.on_line(json.dumps({"type": kind, **values}, ensure_ascii=False))

    async def start(self) -> None:
        if self.context.web is None:
            raise ControlError(
                "This web harness needs the server's Chrome extension bridge"
            )
        if self.context.extra_mcp:
            raise ControlError(
                "Web harnesses support aegis call, not extra MCP transports yet"
            )
        self._active = True
        if self.context.mcp:
            self._auth = CallAuth(self.context.mcp)
        self._emit(
            "init",
            session_id=self._id,
            harness=self.provider.label,
            cwd=str(self.context.cwd),
        )
        self._runner = asyncio.create_task(self._serve())

    async def send(self, text: str) -> None:
        if not self._active:
            raise ConnectionError("The web harness is stopped")
        self._queue.put_nowait(text)

    async def catalog(self) -> Catalog:
        return Catalog(
            (),
            (
                Model(
                    "web",
                    "web",
                    self.provider.label,
                    "Model and modes are selected in Chrome",
                    (),
                ),
            ),
        )

    async def set(self, kind: str, value: str) -> None:
        if kind == "permission":
            if value not in ("read", "write", "auto", "full"):
                raise ControlError(f"Unknown permission {value!r}")
            self.permission = value
            if value in ("read", "write") and self.pid is not None:
                await self.interrupt()
            return
        if kind == "model" and value == "web":
            return
        raise ControlError(f"Choose this web harness's {kind} in its Chrome tab")

    async def _state(self, state: str, text: str) -> None:
        if self._state_text == (state, text):
            return
        self._state_text = (state, text)
        if self.context.web:
            await self.context.web(
                "state",
                {
                    "state": state,
                    "text": text,
                    "url": self.ledger.data.get("conversation", {}).get("url", ""),
                },
            )

    async def _bridge(self, op: str, args: dict) -> dict:
        assert self.context.web is not None
        while self._active:
            try:
                result = await self.context.web(op, args)
                return result
            except CallError as e:
                if e.code not in TRANSIENT:
                    raise
                await self._state(
                    "waiting" if e.code == "chrome_unavailable" else "needs_you",
                    e.message,
                )
                await asyncio.sleep(POLL_S)
        raise asyncio.CancelledError

    async def _observe(self) -> dict:
        expected = self.ledger.data.get("conversation", {})
        while True:
            page = await self._bridge("observe", {"url": expected.get("url")})
            if not page.get("intervention"):
                break
            await self._state("needs_you", str(page["intervention"]))
            await asyncio.sleep(POLL_S)
        if not isinstance(page.get("messages"), list) or not isinstance(
            page.get("users"), list
        ):
            raise RecoveryError("The provider layout is not supported; no command ran")
        url = page.get("url", "")
        if not self.provider.accepts(url):
            raise RecoveryError("The backing tab left the expected provider")
        conversation = self.provider.conversation(url)
        if old := self.provider.conversation(expected.get("url", "")):
            if old != conversation:
                raise RecoveryError(
                    "The backing tab changed conversation; return to the saved chat"
                )
        elif conversation:
            self.ledger.update(conversation={**expected, "url": conversation})
        known = self.ledger.data.get("observed", {})
        ids = [m["id"] for m in page["messages"]]
        if set(known) - set(ids):
            raise RecoveryError(
                "Previously observed answers disappeared; reconcile the source conversation before continuing"
            )
        if len(ids) != len(set(ids)):
            raise RecoveryError(
                "Provider message identity is ambiguous; no command ran"
            )
        for message in page["messages"]:
            if message["complete"] and message["id"] in known:
                if known[message["id"]] != _hash(message["markdown"]):
                    raise RecoveryError(
                        "An already observed answer changed; reconcile the source conversation"
                    )
        users = dict(self.ledger.data.get("users", {}))
        own = set(self.ledger.data.get("submitted", []))
        manual = list(self.ledger.data.get("manual_pending", []))
        for user in page["users"]:
            digest = _user_hash(user["text"])
            previous = users.get(user["id"])
            if previous is not None and previous != digest:
                raise RecoveryError(
                    "A source user message changed; reconcile the Chrome conversation"
                )
            if (
                previous is None
                and digest not in own
                and self.ledger.data.get("primed")
            ):
                self._emit("manual", id=user["id"], text=user["text"])
                manual.append(user["id"])
            users[user["id"]] = digest
        if users != self.ledger.data.get("users", {}):
            self.ledger.update(users=users, manual_pending=manual)
        await self._state("connected", f"{self.provider.label} · Chrome connected")
        return page

    def _remember(self, message: dict) -> None:
        observed = dict(self.ledger.data.get("observed", {}))
        observed[message["id"]] = _hash(message["markdown"])
        messages = dict(self.ledger.data.get("messages", {}))
        messages[message["id"]] = dict(message)
        self.ledger.update(observed=observed, messages=messages)

    def _prime(self) -> str:
        sections = [self.context.system_prompt or ""]
        for directory in reversed((self.context.cwd, *self.context.cwd.parents)):
            for name in ("AGENTS.md", "CLAUDE.md"):
                path = directory / name
                if path.is_file():
                    sections.append(f"Instructions from {path}:\n{path.read_text()}")
        sections.append(BRIEF.format(cwd=self.context.cwd, permission=self.permission))
        return "\n\n".join(part for part in sections if part)

    async def _prepare(self) -> None:
        if uncertain := self.ledger.uncertain():
            raise RecoveryError(
                f"Execution outcome is uncertain ({', '.join(uncertain)}); resolve it before continuing"
            )
        if not self._connected:
            await self._state(
                "waiting", f"Waiting for the {self.provider.label} Chrome tab"
            )
            saved = self.ledger.data.get("conversation", {})
            got = await self._bridge("start", {"conversation": saved})
            self.ledger.update(conversation={**got, **saved})
            self._connected = True
        if not self.ledger.data.get("primed"):
            page = await self._observe()
            if not self.ledger.data.get("outbound") and (
                page["messages"] or page["users"]
            ):
                raise RecoveryError(
                    "A new web session needs an empty conversation; no primer was sent"
                )
            prime = (self.ledger.data.get("outbound") or {}).get(
                "text"
            ) or self._prime()
            acknowledgement = await self._exchange(prime, "prime")
            self._remember(acknowledgement)
            self.ledger.update(
                primed=True,
                prime_message=acknowledgement["id"],
                prime_hash=_hash(prime),
                prime_version=1,
                context=self._session_context(),
            )
        current = self._session_context()
        if (
            current != self.ledger.data.get("context", current)
            and not self.ledger.data.get("active")
            and not self.ledger.data.get("outbound")
        ):
            update = (
                f"Aegis session update\nWorking directory: {self.context.cwd}\n"
                f"Permission: {self.permission}\n"
                f"Current role instructions:\n{self.context.system_prompt or ''}\n"
                "Acknowledge with AEGIS_READY. Do not request a command in this acknowledgement."
            )
            acknowledgement = await self._exchange(update, "update")
            self._remember(acknowledgement)
            self.ledger.update(context=current)

    def _session_context(self) -> dict:
        return {
            "cwd": str(self.context.cwd),
            "permission": self.permission,
            "role": _hash(self.context.system_prompt or ""),
        }

    async def _exchange(
        self, text: str, kind: str, execution: str | None = None
    ) -> dict:
        outbound: dict | None = self.ledger.data.get("outbound")
        recovering = outbound is not None
        if outbound is None:
            page = await self._observe()
            outbound = {
                "text": text,
                "kind": kind,
                "execution": execution,
                "before": [m["id"] for m in page["messages"]],
                "phase": "prepared",
            }
            self.ledger.update(outbound=outbound)
        if outbound["phase"] == "prepared":
            await self._bridge(
                "check", {"url": self.ledger.data["conversation"].get("url")}
            )
            outbound["phase"] = "started"
            submitted = list(self.ledger.data.get("submitted", []))
            submitted.append(_user_hash(outbound["text"]))
            self.ledger.update(outbound=outbound, submitted=submitted)
            assert self.context.web is not None
            # A send can fail after the provider accepted it. Never retry it blindly.
            await self.context.web(
                "send",
                {
                    "text": outbound["text"],
                    "url": self.ledger.data["conversation"].get("url"),
                },
            )
            outbound["phase"] = "sent"
            self.ledger.update(outbound=outbound)
        while self._active:
            page = await self._observe()
            new = [m for m in page["messages"] if m["id"] not in outbound["before"]]
            acknowledged = any(
                u["text"] == outbound["text"] for u in page["users"]
            ) or bool(new)
            if (
                recovering
                and not acknowledged
                and outbound["phase"] in ("started", "sent")
            ):
                raise RecoveryError(
                    "Message delivery is uncertain; check the source conversation before retrying"
                )
            execution_id = outbound.get("execution")
            if acknowledged and isinstance(execution_id, str):
                record = self.ledger.get(execution_id)
                if record and record["phase"] == "delivery-pending":
                    self.ledger.delivered(execution_id)
            ready = [m for m in new if m["complete"]]
            if len(new) > 1:
                raise RecoveryError(
                    "Several new provider answers appeared; reconcile manual activity first"
                )
            if ready:
                self.ledger.update(outbound=None)
                return ready[0]
            await asyncio.sleep(POLL_S)
        raise asyncio.CancelledError

    async def _serve(self) -> None:
        while self._active:
            text = await self._queue.get()
            self._turn = asyncio.create_task(self._run_turn(text))
            try:
                await self._turn
            except asyncio.CancelledError:
                if not self._active:
                    break
            except Exception as error:
                self._active = False
                self.context.on_exit(1, [str(error)])
            finally:
                self._turn = None

    async def _run_turn(self, text: str) -> None:
        started = time.monotonic()
        try:
            await self._prepare()
            active: dict | None = self.ledger.data.get("active")
            if active is None and not self.ledger.data.get("outbound"):
                page = await self._observe()
                unseen = [
                    m
                    for m in page["messages"]
                    if m["id"] not in self.ledger.data.get("observed", {})
                ]
                if len(unseen) > 1 or any(not m["complete"] for m in unseen):
                    raise RecoveryError(
                        "Manual Chrome responses are incomplete or ambiguous; reconcile them before sending another task"
                    )
                if unseen:
                    if not self.ledger.data.get("manual_pending"):
                        raise RecoveryError(
                            "An unrequested answer appeared; reconcile regeneration in Chrome before continuing"
                        )
                    active = {"prompt": text, "message": unseen[0], "manual": True}
                    self.ledger.update(active=active, manual_pending=[])
            if (
                active
                and active.get("message")
                and not self.ledger.data.get("outbound")
            ):
                message = active["message"]
            else:
                if active is None:
                    active = {"prompt": text, "message": None}
                    self.ledger.update(active=active)
                    self._emit("echo", text=text)
                message = await self._exchange(active["prompt"], "task")
            while self._active:
                self.ledger.update(active={**active, "message": message})
                try:
                    block = request(message["markdown"])
                except ProtocolError as error:
                    self._remember(message)
                    self._emit("text", id=message["id"], text=str(error))
                    message = await self._exchange(
                        f"Aegis protocol refusal: {error}. No command ran. Request exactly one marked Bash block.",
                        "refusal",
                    )
                    continue
                self._remember(message)
                prose = block.prose if block else message["markdown"]
                if prose:
                    self._emit("text", id=message["id"], text=prose)
                if block is None:
                    if active.get("manual"):
                        active = {"prompt": active["prompt"], "message": None}
                        self.ledger.update(active=active)
                        self._emit("echo", text=active["prompt"])
                        message = await self._exchange(active["prompt"], "task")
                        continue
                    self.ledger.update(active=None)
                    self._emit(
                        "result",
                        error=False,
                        stop_reason="stop",
                        duration_ms=int(1000 * (time.monotonic() - started)),
                    )
                    return
                key = f"{message['id']}/0"
                record = self.ledger.prepare(key, block.code)
                if record["phase"] == "prepared":
                    await self._execute(key, block.code)
                    record = self.ledger.get(key)
                if record is None or record["phase"] not in (
                    "completed",
                    "delivery-pending",
                ):
                    raise RecoveryError(
                        "This execution is already handled or uncertain; none ran again"
                    )
                if record["phase"] == "completed":
                    self.ledger.delivery_pending(key)
                message = await self._exchange(
                    self._result_text(key, record["result"]), "result", execution=key
                )
        except asyncio.CancelledError:
            if self._active:
                self.ledger.update(active=None, outbound=None)
                self._emit(
                    "result",
                    error=False,
                    stop_reason="interrupted",
                    duration_ms=int(1000 * (time.monotonic() - started)),
                )
            raise
        except (RecoveryError, CallError, OSError, ValueError) as error:
            self.context.on_error(str(error), False, text)
            await self._state("needs_you", str(error))
            self._emit(
                "result",
                error=True,
                stop_reason="error",
                text=str(error),
                duration_ms=int(1000 * (time.monotonic() - started)),
            )

    async def _execute(self, key: str, code: str) -> None:
        await self._bridge(
            "check", {"url": self.ledger.data["conversation"].get("url")}
        )
        page = await self._observe()
        source_id = key.rsplit("/", 1)[0]
        latest = page["messages"][-1] if page["messages"] else None
        original = self.ledger.data.get("messages", {}).get(source_id)
        if (
            latest is None
            or original is None
            or latest["id"] != source_id
            or not latest["complete"]
            or latest["markdown"] != original["markdown"]
        ):
            raise RecoveryError(
                "The captured command is no longer the current completed answer; no command ran"
            )
        self.ledger.start(key)
        self._emit("tool_call", id=key, code=code)
        if self.permission in ("read", "write") or not self.context.harness_tools:
            why = (
                f"Permission {self.permission} does not allow free-form Bash"
                if self.context.harness_tools
                else "Harness Bash tools are disabled for this session"
            )
            result = {"stdout": "", "stderr": why, "code": 126}
            self.ledger.complete(key, result)
            self._emit(
                "tool_output", id=key, text=self._result_text(key, result), error=True
            )
            return
        env = session_env(self.context) or dict(os.environ)
        # Do not inherit the controller's credential or print the web session's token in env.
        for name in (TOKEN_VAR, URL_VAR, AUTH_VAR):
            env.pop(name, None)
        if self._auth:
            env[AUTH_VAR] = str(self._auth.path)
        self._child = await launch.child(
            ["bash", "-c", code],
            cwd=self.context.cwd,
            env=env,
            stdout=launch.PIPE,
            stderr=launch.PIPE,
            new_session=True,
            cgroup=self.context.cgroup,
            oom=True,
            via=self.context.via,
        )
        self._pgid = self._child.pid
        prefix = self._root / _hash(key)
        stdout = asyncio.create_task(
            self._capture(self._child.stdout, prefix.with_suffix(".stdout"))
        )
        stderr = asyncio.create_task(
            self._capture(self._child.stderr, prefix.with_suffix(".stderr"))
        )
        completion = asyncio.gather(self._child.wait(), stdout, stderr)
        interrupted = False
        try:
            try:
                code_number, out, err = await asyncio.wait_for(
                    asyncio.shield(completion), COMMAND_TIMEOUT_S
                )
            except (TimeoutError, asyncio.CancelledError) as error:
                interrupted = isinstance(error, asyncio.CancelledError)
                await self._kill()
                code_number = 130 if interrupted else 124
                try:
                    _, out, err = await asyncio.wait_for(
                        asyncio.shield(completion), OUTPUT_DRAIN_S
                    )
                except TimeoutError:
                    completion.cancel()
                    await asyncio.gather(completion, return_exceptions=True)
                    out = self._saved_output(prefix.with_suffix(".stdout"))
                    err = self._saved_output(prefix.with_suffix(".stderr"))
                    err += "\n[Output drain stopped after the command deadline]"
            result = {"stdout": out, "stderr": err, "code": code_number}
            self.ledger.complete(key, result)
            self._emit(
                "tool_output",
                id=key,
                text=self._result_text(key, result),
                error=code_number != 0,
            )
        finally:
            if not completion.done():
                await self._kill()
                completion.cancel()
                await asyncio.gather(completion, return_exceptions=True)
            self._pgid = None
            self._child = None
        if interrupted:
            # The result is known, but a stopped turn must not resume its delivery loop.
            record = self.ledger.get(key)
            assert record is not None
            record["phase"] = "interrupted"
            self.ledger.update(active=None, outbound=None)
            raise asyncio.CancelledError

    async def _capture(self, stream: asyncio.StreamReader | None, path: Path) -> str:
        assert stream is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        token = self.context.mcp[1].encode() if self.context.mcp else b""
        carry = b""
        preview = bytearray()
        total = 0
        with os.fdopen(
            os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb"
        ) as output:
            while chunk := await stream.read(16384):
                data = carry + chunk
                if token:
                    data = data.replace(token, b"[redacted session credential]")
                    keep = next(
                        (
                            n
                            for n in range(min(len(token) - 1, len(data)), 0, -1)
                            if data.endswith(token[:n])
                        ),
                        0,
                    )
                else:
                    keep = 0
                carry = data[-keep:] if keep else b""
                data = data[:-keep] if keep else data
                output.write(data)
                preview.extend(data[: max(0, OUTPUT_BYTES - len(preview))])
                total += len(data)
            output.write(carry)
            preview.extend(carry[: max(0, OUTPUT_BYTES - len(preview))])
            total += len(carry)
        text = preview.decode("utf-8", errors="replace")
        if total > OUTPUT_BYTES:
            text += f"\n[Truncated at {OUTPUT_BYTES} bytes; full output: {path}]"
        return text

    @staticmethod
    def _result_text(key: str, result: dict) -> str:
        return f"Aegis Bash result · execution {key}\nexit_code: {result['code']}\nstdout:\n{result['stdout']}\nstderr:\n{result['stderr']}\n"

    @staticmethod
    def _saved_output(path: Path) -> str:
        with path.open("rb") as output:
            text = output.read(OUTPUT_BYTES).decode("utf-8", errors="replace")
        if path.stat().st_size > OUTPUT_BYTES:
            text += f"\n[Truncated at {OUTPUT_BYTES} bytes; full output: {path}]"
        return text

    async def _kill(self) -> None:
        if self._pgid is not None:
            await launch.signal_process_async(self._pgid, signal.SIGKILL, group=True)

    async def interrupt(self) -> None:
        if self._turn and not self._turn.done():
            self._turn.cancel()
            with suppress(asyncio.CancelledError):
                await self._turn
        if self.context.web and self._connected:
            with suppress(CallError):
                await self.context.web(
                    "stop", {"url": self.ledger.data.get("conversation", {}).get("url")}
                )

    async def terminate(self) -> None:
        self._active = False
        if self._runner:
            self._runner.cancel()
            with suppress(asyncio.CancelledError):
                await self._runner
        await self._kill()
        if self.context.web:
            await self.context.web("release", {})
        if self._auth:
            self._auth.close()
            self._auth = None


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _user_hash(text: str) -> str:
    return _hash(" ".join(text.split()))
