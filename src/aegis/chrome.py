"""The person's own Chrome, paired with this server through aegis's extension
(docs/superpowers/specs/2026-11-11-chrome-extension-design.html).

Pairing: Settings → Chrome mints a one-time code, valid ten minutes and kept
in memory only. The extension trades it at ``POST /chrome/pair`` for a device
token of its own. The token opens ``/chrome/socket`` and nothing else: it is
not a principal, so it cannot call an operation or open ``/ws``. Only its
SHA-256 is kept, in ``<state>/chrome.json`` (mode 0600), so a leaked state
directory does not pair anyone.

Presence: a device is connected while its socket is open. The extension pings
every 20 s to keep its service worker alive (Chrome stops an idle one after
30 s), and each ping moves its last-seen time. The ``chrome`` channel carries
every paired browser with its dot for the Fleet and Settings.

The theme: the page tells the server the CSS variables of the theme the
person picked (``chrome.theme``), the server keeps the last set in
chrome.json and sends it to each extension when it connects and whenever it
changes, and the extension keeps it for when it is offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import time
from collections.abc import Awaitable, Callable, Coroutine
from typing import Any
from pathlib import Path

FILE = "chrome.json"
CODE_TTL_S = 600
# No 0/O or 1/I/L: the person types the code from one screen into another.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 8

Send = Callable[[dict], Awaitable[None]]


class PairError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def normal_code(code: str) -> str:
    """A code as typed: any case, with or without its dash or spaces."""
    return "".join(c for c in code.upper() if c.isalnum())


class Chrome:
    """Paired browsers, their pairing codes and their open sockets."""

    def __init__(
        self,
        state_root: Path,
        publish: Callable[[str, list[dict]], None],
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = state_root / FILE
        self._publish = publish
        self._clock = clock
        self.codes: dict[str, float] = {}  # code -> expiry
        self.devices: dict[str, dict] = {}  # id -> its record
        self.theme: dict[str, str] = {}
        self.sockets: dict[str, Send] = {}  # id -> how to send to it
        self.closers: dict[str, Callable[[], Coroutine[Any, Any, None]]] = {}
        self._load()

    # -- the file -----------------------------------------------------------------

    def _load(self) -> None:
        try:
            doc = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except ValueError:
            return  # a torn file pairs nobody; pairing again rewrites it
        self.devices = {d["id"]: d for d in doc.get("devices", []) if "id" in d}
        theme = doc.get("theme")
        self.theme = theme if isinstance(theme, dict) else {}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(
                {"devices": list(self.devices.values()), "theme": self.theme},
                f,
                indent=2,
            )
        os.replace(tmp, self.path)

    # -- pairing ------------------------------------------------------------------

    def new_code(self) -> dict:
        """A one-time code for the extension's popup, shown as XXXX-XXXX."""
        now = self._clock()
        self.codes = {c: exp for c, exp in self.codes.items() if exp > now}
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LEN))
        self.codes[code] = now + CODE_TTL_S
        return {
            "code": f"{code[:4]}-{code[4:]}",
            "expires_at": now + CODE_TTL_S,
        }

    def pair(self, code: str, name: str, browser: str = "") -> tuple[str, dict]:
        """Trade a code for a device token: the token, once, and the record."""
        code = normal_code(code)
        exp = self.codes.pop(code, None)
        if exp is None or exp < self._clock():
            raise PairError(
                "bad_code",
                "that code is wrong or expired: get a new one in Settings → Chrome",
            )
        token = "aegc_" + secrets.token_urlsafe(32)
        now = self._clock()
        dev = {
            "id": "c-" + secrets.token_hex(4),
            "name": (name or browser or "Chrome").strip()[:80],
            "browser": browser[:40],
            "token_sha": _sha(token),
            "paired_at": now,
            "last_seen": now,
            "version": "",
        }
        self.devices[dev["id"]] = dev
        self._save()
        self.changed()
        return token, dev

    def device_for(self, token: str) -> dict | None:
        sha = _sha(token)
        for d in self.devices.values():
            if secrets.compare_digest(d["token_sha"], sha):
                return d
        return None

    async def unpair(self, device_id: str) -> None:
        """Revoke a device's token and close its socket."""
        if self.devices.pop(device_id, None) is None:
            return
        self._save()
        send = self.sockets.get(device_id)
        close = self.closers.get(device_id)
        if send is not None:
            try:
                await send({"t": "unpaired"})
            except Exception:
                pass
        if close is not None:
            await close()
        self.changed()

    # -- presence -----------------------------------------------------------------

    def connected(
        self,
        device_id: str,
        send: Send,
        close: Callable[[], Coroutine[Any, Any, None]],
        version: str,
    ) -> None:
        old = self.closers.get(device_id)
        self.sockets[device_id] = send
        self.closers[device_id] = close
        if old is not None:
            # A second socket from the same extension (a restarted worker that
            # reconnected before the old one timed out) replaces the first.
            asyncio.get_running_loop().create_task(old())
        dev = self.devices[device_id]
        dev["version"] = version[:20]
        self.seen(device_id, save=True)

    def disconnected(self, device_id: str, send: Send) -> None:
        if self.sockets.get(device_id) is not send:
            return  # a replaced socket closing late
        self.sockets.pop(device_id, None)
        self.closers.pop(device_id, None)
        if device_id in self.devices:
            self.seen(device_id, save=True)

    def seen(self, device_id: str, save: bool = False) -> None:
        dev = self.devices.get(device_id)
        if dev is None:
            return
        dev["last_seen"] = self._clock()
        if save:
            self._save()
        self.changed()

    # -- the theme ----------------------------------------------------------------

    async def set_theme(self, vars: dict[str, str]) -> None:
        if vars == self.theme:
            return
        self.theme = vars
        self._save()
        for send in list(self.sockets.values()):
            try:
                await send({"t": "theme", "vars": vars})
            except Exception:
                pass

    # -- the channel --------------------------------------------------------------

    def wire(self) -> dict:
        return {
            "devices": [
                {
                    "id": d["id"],
                    "name": d["name"],
                    "browser": d.get("browser", ""),
                    "version": d.get("version", ""),
                    "paired_at": d["paired_at"],
                    "last_seen": d["last_seen"],
                    "connected": d["id"] in self.sockets,
                    "tabs": 0,
                }
                for d in self.devices.values()
            ]
        }

    def changed(self) -> None:
        self._publish("chrome", [{"set": self.wire()}])


# -- the extension, as the server serves it -------------------------------------

EXTENSION_DIR = Path(__file__).parent / "extension"
CLIENT_DIR = Path(__file__).parent / "client"
# What the extension takes from the client as it is, under the same paths, so
# fonts.css's ../fonts/ links hold: the mark and its animation, and the fonts.
SHARED = ("js/glyphs.js", "js/mark.js", "css/fonts.css", "themes/ink.css")


def _gorgon_css() -> str:
    """The Gorgoneion's rules from client.css: its colour, the eye's look and
    blink, and their stop under reduced motion."""
    keep = (
        ".aegis .gorgon{",
        ".aegis .gorgon ",
        "@keyframes gorgon-",
        "@media (prefers-reduced-motion:reduce){.aegis .gorgon",
    )
    lines = (CLIENT_DIR / "css" / "client.css").read_text().splitlines()
    return "\n".join(line for line in lines if line.startswith(keep)) + "\n"


def extension_files() -> dict[str, bytes]:
    """The unpacked extension: its own files, then the client's it shares
    under ``aegis/``."""
    out = {
        str(p.relative_to(EXTENSION_DIR)): p.read_bytes()
        for p in sorted(EXTENSION_DIR.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }
    for rel in SHARED:
        out[f"aegis/{rel}"] = (CLIENT_DIR / rel).read_bytes()
    for p in sorted((CLIENT_DIR / "fonts").glob("*")):
        out[f"aegis/fonts/{p.name}"] = p.read_bytes()
    out["aegis/css/gorgon.css"] = _gorgon_css().encode()
    return out


def extension_zip() -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in extension_files().items():
            z.writestr(f"aegis-chrome/{name}", data)
    return buf.getvalue()
