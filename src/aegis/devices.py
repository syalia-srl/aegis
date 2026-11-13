"""Phones that let their owner's agents use them (#1058, #1060).

A device is the aegis Android app on a phone, paired to one principal. Pairing
gives the server the public half of a P-256 key the phone made in its Android
Keystore; the private half never leaves the phone. The phone then opens a
socket to ``/device`` (web.py), signs the nonce the server sends, and waits for
calls. Both connections start on the phone, so the server never reaches it.

The server only routes. Which operations a phone runs, and whether it asks the
person first, is the phone's to decide: an agent and the server are the parts a
prompt injection can steer, so a grant the server held would be one it could
also forge (docs/superpowers/specs/2026-11-13-android-app-design.html, 02).

The rows live in ``<state>/devices.json``, mode 0600 like the push
subscriptions. A call's result is never stored here; it goes to the caller.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from .ops import OpError
from .push import _write_private

log = logging.getLogger("aegis.devices")

# The newest shell this server knows of; a phone below it is told so.
LATEST_SHELL = 1
NAME_MAX = 60
KEY_MAX = 200  # an SPKI DER P-256 key is 91 bytes

Send = Callable[[dict], Awaitable[None]]


def b64d(s: str) -> bytes:
    s = "".join(s.split())
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def check_key(public_key: str) -> bytes:
    """The DER of a P-256 public key given as base64url SPKI, or ValueError."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    try:
        der = b64d(public_key)
    except (ValueError, TypeError) as e:
        raise ValueError("public_key is not base64url") from e
    if len(der) > KEY_MAX:
        raise ValueError("public_key is too long")
    key = load_der_public_key(der)
    if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
        raise ValueError("public_key is not a P-256 key")
    return der


class Live:
    """One connected phone: how to send it a frame, and its calls in flight."""

    def __init__(self, send: Send, close: Callable[[], None] | None = None) -> None:
        self.send = send
        self.close = close
        self.pending: dict[str, asyncio.Future] = {}


class Devices:
    """The paired phones, read once and written whole on each change, and the
    sockets of those connected now."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._rows: list[dict] = []
        self._live: dict[str, Live] = {}
        if path.exists():
            try:
                rows = json.loads(path.read_text())
                self._rows = [r for r in rows if isinstance(r, dict) and "id" in r]
            except (OSError, ValueError):
                self._rows = []

    def _save(self) -> None:
        _write_private(self.path, json.dumps(self._rows, indent=1))

    # -- the store ------------------------------------------------------------

    def get(self, id: str) -> dict | None:
        return next((r for r in self._rows if r["id"] == id), None)

    def of_owner(self, owner: str) -> list[dict]:
        return [self.view(r) for r in self._rows if r["owner"] == owner]

    def view(self, row: dict) -> dict:
        """A row as people and agents see it: no key."""
        out = {k: v for k, v in row.items() if k != "public_key"}
        out["online"] = row["id"] in self._live
        return out

    def pair(self, owner: str, name: str, public_key: str) -> dict:
        """Record a phone's key for ``owner`` and return its row. The same key
        paired again is the same device, now ``owner``'s, with the new name."""
        der = b64e(check_key(public_key))
        name = " ".join(name.split())[:NAME_MAX] or "phone"
        row = next((r for r in self._rows if r["public_key"] == der), None)
        if row is None:
            row = {
                "id": "d_" + secrets.token_hex(4),
                "created_at": int(time.time()),
                "last_seen": None,
                "shell": None,
                "api": None,
                "model": None,
                "ops": [],
            }
            self._rows.append(row)
        row.update(owner=owner, name=self._free_name(owner, name, row), public_key=der)
        self._save()
        return self.view(row)

    def _free_name(self, owner: str, name: str, row: dict) -> str:
        """``name``, or ``name-2``... when another of the owner's phones has it."""
        taken = {r["name"] for r in self._rows if r is not row and r["owner"] == owner}
        if name not in taken:
            return name
        n = 2
        while f"{name}-{n}" in taken:
            n += 1
        return f"{name}-{n}"

    def verify(self, id: str, nonce: bytes, sig: str) -> bool:
        """Whether ``sig`` (base64url DER ECDSA over ``nonce`` with SHA-256)
        was made by device ``id``'s key."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import load_der_public_key

        row = self.get(id)
        if row is None:
            return False
        try:
            key = load_der_public_key(b64d(row["public_key"]))
            assert isinstance(key, ec.EllipticCurvePublicKey)
            key.verify(b64d(sig), nonce, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError, TypeError):
            return False
        return True

    def remove(self, id: str) -> None:
        self._rows = [r for r in self._rows if r["id"] != id]
        self._save()
        self._drop(id)

    def remove_owner(self, owner: str) -> None:
        gone = [r["id"] for r in self._rows if r["owner"] == owner]
        if gone:
            self._rows = [r for r in self._rows if r["owner"] != owner]
            self._save()
        for id in gone:
            self._drop(id)

    # -- live sockets ----------------------------------------------------------

    def attach(
        self, id: str, hello: dict, send: Send, close: Callable[[], None] | None = None
    ) -> Live | None:
        """A phone proved its key: record what it said and route calls to it.
        A second socket for one phone replaces the first. None when the device
        was removed meanwhile."""
        row = self.get(id)
        if row is None:
            return None
        ops = hello.get("ops")
        row.update(
            last_seen=int(time.time()),
            shell=hello.get("shell") if isinstance(hello.get("shell"), int) else None,
            api=hello.get("api") if isinstance(hello.get("api"), int) else None,
            model=str(hello.get("model") or "")[:NAME_MAX] or None,
            ops=[o for o in ops if isinstance(o, str)][:200]
            if isinstance(ops, list)
            else [],
        )
        self._save()
        self._drop(id)
        live = self._live[id] = Live(send, close)
        return live

    def detach(self, id: str, live: Live) -> None:
        """The socket ``live`` closed; a newer one for the same phone stays."""
        if self._live.get(id) is live:
            del self._live[id]
        self._fail(live)

    def _drop(self, id: str) -> None:
        live = self._live.pop(id, None)
        if live is not None:
            self._fail(live)
            if live.close is not None:
                live.close()

    @staticmethod
    def _fail(live: Live) -> None:
        for fut in live.pending.values():
            if not fut.done():
                fut.set_exception(OpError("device_offline", "the phone disconnected"))
        live.pending.clear()

    def on_reply(self, live: Live, frame: dict) -> None:
        fut = live.pending.pop(str(frame.get("id")), None)
        if fut is None or fut.done():
            return
        err = frame.get("error")
        if isinstance(err, dict):
            fut.set_exception(
                OpError(
                    str(err.get("code") or "android_error"),
                    str(err.get("message") or ""),
                )
            )
        else:
            fut.set_result(frame.get("result"))

    async def call(
        self, id: str, session: str, op: str, args: dict, timeout: float
    ) -> object:
        live = self._live.get(id)
        if live is None:
            raise OpError(
                "device_offline",
                "the phone is not connected; ask the person to open the aegis app",
            )
        cid = secrets.token_hex(6)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        live.pending[cid] = fut
        try:
            await live.send(
                {"t": "call", "id": cid, "session": session, "op": op, "args": args}
            )
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise OpError(
                "device_timeout", f"the phone did not answer within {timeout:g} s"
            ) from None
        finally:
            live.pending.pop(cid, None)

    def online(self, owner: str) -> list[str]:
        return [
            r["id"] for r in self._rows if r["owner"] == owner and r["id"] in self._live
        ]

    async def notify(self, owner: str, payload: dict) -> None:
        """Send an attention notification to each of ``owner``'s phones online."""
        for id in self.online(owner):
            live = self._live.get(id)
            if live is None:
                continue
            try:
                await live.send({"t": "notify"} | payload)
            except Exception:
                log.debug(
                    "aegis: a notification to device %s failed", id, exc_info=True
                )
