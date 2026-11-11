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

Consent: a grant is one tab and one agent session, and the extension is its
authority. It sends the server the tabs that have a grant, and only those:
a tab with none never leaves the browser, not even its title. The server
keeps a mirror (``tabs``) for the tools and the panels, which the extension
replaces on every hello, so grants outlive a sleeping laptop. A grant starts
with the person: sharing a tab from the popup, or giving one an agent asked
for (``browser_request``, ``browser_open(where="person")``). A request is
answered in the popup or on its card in aegis, whichever is first; the server
resolves it once and tells the other side. Either side revokes, and the side
that did tells the other; neither echoes what it was told.

Driving: an agent's browser_* call on ``chrome:<name>`` goes to the tab's
browser as a ``call`` and comes back as its ``result``. The server checks the
grant first and the extension checks it again, since it is the authority. The
extension sends the tab's navigations, console errors and failed requests as
``event`` messages, and the server keeps them, with the agents' actions, in
the tab's log, as it does for its own browsers.
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
CALL_TIMEOUT_S = 45  # a goto waits up to 30 s for the page to load
LOG_MAX = 500  # events a tab's log keeps in memory

Send = Callable[[dict], Awaitable[None]]


class CallError(Exception):
    """A call on a person's tab that failed, with the code an agent reads."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


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
        # name -> {name, title, url, grants: [log_id], device}: shared tabs only.
        self.tabs: dict[str, dict] = {}
        # id -> {id, log_id, handle, kind: tab|open, match, url, why, ts}
        self.requests: dict[str, dict] = {}
        # The tabs each session was ever given, to tell revoked from not_granted.
        self.had: dict[str, set[str]] = {}
        # name -> {rev, events}: each tab's log, as a server browser's.
        self.logs: dict[str, dict] = {}
        self.calls: dict[str, tuple[str, asyncio.Future]] = {}  # id -> device, reply
        # Set by chrome_ops: the sessions a tab can be shared with, and what
        # happens when a request is answered or a grant starts or ends.
        self.list_sessions: Callable[[], list[dict]] = lambda: []
        self.on_answer: Callable[[dict, str, dict | None], None] = lambda *_: None
        self.on_grants: Callable[[], None] = lambda: None
        self.on_nomatch: Callable[[dict], object] = lambda _: None
        # An agent's request, with its card (chrome_ops): browser_open's too.
        self.ask_for: Callable[..., Awaitable[dict]]
        # An agent's browser_* tool on one of its tabs (chrome_ops).
        self.drive: Callable[..., Awaitable[object]]
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
        self.tabs = {n: t for n, t in self.tabs.items() if t.get("device") != device_id}
        self._grants_changed()

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
        for cid, (dev, fut) in list(self.calls.items()):
            if dev == device_id and not fut.done():
                fut.set_exception(
                    CallError("chrome_unavailable", "your browser disconnected")
                )
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

    # -- tabs and grants ----------------------------------------------------------

    def connected_any(self) -> bool:
        return bool(self.sockets)

    def granted(self, log_id: str) -> list[dict]:
        """The tabs a session may use."""
        return [t for t in self.tabs.values() if log_id in t["grants"]]

    def _tab(self, device_id: str, tab: dict) -> dict | None:
        name = tab.get("name")
        if not isinstance(name, str) or not name:
            return None
        row = self.tabs.get(name)
        if row is None or row.get("device") != device_id:
            # A name is one browser's: another paired browser's tab of the
            # same name is a different tab, and its grants do not carry over.
            row = self.tabs[name] = {"name": name, "title": "", "url": "", "grants": []}
        row["device"] = device_id
        row["title"] = str(tab.get("title") or "")[:200]
        row["url"] = str(tab.get("url") or "")[:2000]
        return row

    def sync_tabs(self, device_id: str, tabs: Any) -> None:
        """The extension's shared tabs after a hello: they replace its own."""
        self.tabs = {n: t for n, t in self.tabs.items() if t.get("device") != device_id}
        for tab in tabs if isinstance(tabs, list) else []:
            if isinstance(tab, dict) and (row := self._tab(device_id, tab)):
                given = tab.get("grants")
                row["grants"] = [
                    g
                    for g in (given if isinstance(given, list) else [])
                    if isinstance(g, str)
                ]
        self._grants_changed()

    def _grants_changed(self) -> None:
        self.tabs = {n: t for n, t in self.tabs.items() if t["grants"]}
        # A later tab of the same name is another tab: it starts a clean log.
        self.logs = {n: book for n, book in self.logs.items() if n in self.tabs}
        for name, t in self.tabs.items():
            for g in t["grants"]:
                self.had.setdefault(g, set()).add(name)
        self.changed()
        self.on_grants()

    async def revoke(self, name: str, log_id: str | None = None) -> None:
        """End a grant, or every grant on a tab, from aegis."""
        tab = self.tabs.get(name)
        if tab is None:
            return
        tab["grants"] = (
            [] if log_id is None else [g for g in tab["grants"] if g != log_id]
        )
        self._grants_changed()
        await self._send(
            tab.get("device"), {"t": "revoke", "tab": name, "log_id": log_id}
        )

    # -- requests -----------------------------------------------------------------

    async def ask(
        self, log_id: str, handle: str, kind: str, match: str, url: str, why: str
    ) -> dict:
        req = {
            "id": "rq-" + secrets.token_hex(4),
            "log_id": log_id,
            "handle": handle,
            "kind": kind,
            "match": match,
            "url": url,
            "why": why,
            "ts": self._clock(),
        }
        self.requests[req["id"]] = req
        self.changed()
        await self._send(None, {"t": "request", "request": req})
        return req

    def resolve(self, rid: str, outcome: str, tab: dict | None = None) -> dict | None:
        """Answer a request once: granted with its tab, or denied."""
        req = self.requests.pop(rid, None)
        if req is None:
            return None
        self.changed()
        self.on_answer(req, outcome, tab)
        return req

    async def answer(self, rid: str, give: bool) -> None:
        """The person's answer on the card in aegis. A refusal is final here;
        a give goes to the extension, which picks the tab or opens the page
        and sends the grant back."""
        if rid not in self.requests:
            raise PairError("gone", "that request was already answered")
        if give:
            # One browser gives, never all of them: the one seen last.
            live = [d for d in self.devices.values() if d["id"] in self.sockets]
            if not live:
                raise PairError("chrome_unavailable", "your browser is not connected")
            last = max(live, key=lambda d: d["last_seen"])
            await self._send(last["id"], {"t": "answer", "request": rid, "give": True})
            return
        self.resolve(rid, "denied")
        await self._send(None, {"t": "cancel", "request": rid})

    async def _send(self, device_id: str | None, msg: dict) -> None:
        targets = (
            list(self.sockets.values())
            if device_id is None
            else [s for d, s in self.sockets.items() if d == device_id]
        )
        for send in targets:
            try:
                await send(msg)
            except Exception:
                pass

    async def handle(self, device_id: str, msg: dict) -> dict | None:
        """One message from the extension; the reply to send back, if any."""
        kind = msg.get("t")
        if kind == "ping":
            self.seen(device_id)
            return {"t": "pong"}
        if kind == "sessions":
            return {
                "t": "sessions",
                "id": msg.get("id"),
                "sessions": self.list_sessions(),
            }
        if kind == "grant":
            log_id = msg.get("log_id")
            row = self._tab(device_id, msg.get("tab") or {})
            if row is None or not isinstance(log_id, str):
                return None
            if log_id not in row["grants"]:
                row["grants"].append(log_id)
            rid = msg.get("request")
            if isinstance(rid, str) and self.resolve(rid, "granted", row):
                # Another paired browser drops the request it still shows.
                await self._send(None, {"t": "cancel", "request": rid})
            self._grants_changed()
            return None
        if kind == "revoke":
            tab = self.tabs.get(str(msg.get("tab")))
            if tab is not None:
                who = msg.get("log_id")
                tab["grants"] = (
                    [] if who is None else [g for g in tab["grants"] if g != who]
                )
                self._grants_changed()
            return None
        if kind == "tab":
            t = msg.get("tab") or {}
            if isinstance(t, dict) and t.get("name") in self.tabs:
                self._tab(device_id, t)
                self.changed()
            return None
        if kind == "nomatch":
            # Give from aegis, and no tab's title or address holds the words:
            # the extension gives nothing it was not shown, and the request
            # waits in its popup for the person to pick a tab.
            req = self.requests.get(str(msg.get("request")))
            if req is not None:
                self.on_nomatch(req)
            return None
        if kind == "deny":
            self.resolve(str(msg.get("request")), "denied")
            return None
        if kind == "result":
            entry = self.calls.get(str(msg.get("id")))
            if entry is not None and entry[0] == device_id and not entry[1].done():
                if msg.get("ok"):
                    entry[1].set_result(msg.get("value"))
                else:
                    entry[1].set_exception(
                        CallError(
                            str(msg.get("code") or "call_failed"),
                            str(msg.get("message") or "the call failed"),
                        )
                    )
            return None
        if kind == "event":
            tab = self.tabs.get(str(msg.get("tab")))
            ev = msg.get("event")
            if (
                tab is not None
                and tab.get("device") == device_id
                and isinstance(ev, dict)
            ):
                self.log(tab["name"], ev, who=str(msg.get("who") or "page"))
            return None
        return None

    # -- driving ------------------------------------------------------------------

    def tab_for(self, log_id: str, name: str) -> dict:
        """The tab a session names, if it is granted to it now."""
        tab = self.tabs.get(name)
        if tab is not None and log_id in tab["grants"]:
            if tab.get("device") not in self.sockets:
                raise CallError(
                    "chrome_unavailable",
                    "the person's browser is not connected to aegis; wait for it",
                )
            return tab
        if name in self.had.get(log_id, set()):
            raise CallError(
                "revoked",
                f"chrome:{name} is no longer yours: the person stopped sharing it,"
                " closed it or disconnected you",
            )
        raise CallError(
            "not_granted",
            f"chrome:{name} is not a tab the person gave you; see browser_list,"
            " or ask with browser_request",
        )

    async def call(
        self,
        log_id: str,
        name: str,
        op: str,
        args: dict,
        timeout: float = CALL_TIMEOUT_S,
    ) -> Any:
        """One tool call on a person's tab, answered by its browser."""
        tab = self.tab_for(log_id, name)
        cid = "k-" + secrets.token_hex(6)
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self.calls[cid] = (tab["device"], fut)
        try:
            await self._send(
                tab["device"],
                {"t": "call", "id": cid, "tab": name, "log_id": log_id, "op": op,
                 "args": args},
            )  # fmt: skip
            return await asyncio.wait_for(fut, timeout)
        except TimeoutError:
            raise CallError(
                "chrome_timeout", f"your browser did not answer {op} in {timeout:.0f} s"
            ) from None
        finally:
            self.calls.pop(cid, None)

    def log(self, name: str, ev: dict, who: str) -> int:
        """Add an event to a tab's log; its revision."""
        book = self.logs.setdefault(name, {"rev": 0, "events": []})
        book["rev"] += 1
        row = {k: v for k, v in ev.items() if k not in ("rev", "ts", "who")}
        row |= {"rev": book["rev"], "ts": self._clock(), "who": who}
        book["events"] = (book["events"] + [row])[-LOG_MAX:]
        d = self.path.parent / "chrome" / name
        try:
            d.mkdir(parents=True, exist_ok=True)
            with (d / "events.jsonl").open("a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass
        return book["rev"]

    def events(self, name: str, since: int = 0) -> tuple[int, list[dict]]:
        book = self.logs.get(name, {"rev": 0, "events": []})
        return book["rev"], [e for e in book["events"] if e["rev"] > since]

    def acted(self, name: str, handle: str, line: str) -> int:
        """An agent's action on a tab: logged, and shown in its panel."""
        tab = self.tabs.get(name)
        if tab is not None:
            tab["last"] = f"{handle} {line}"
            self.changed()
        return self.log(name, {"kind": "agent", "what": f"{handle} {line}"}, "agent")

    def shots_dir(self, name: str) -> Path:
        return self.path.parent / "chrome" / name / "shots"

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
                    "tabs": sum(
                        1 for t in self.tabs.values() if t.get("device") == d["id"]
                    ),
                }
                for d in self.devices.values()
            ],
            "tabs": [
                {k: t.get(k, "") for k in ("name", "title", "url", "grants", "last")}
                for t in self.tabs.values()
            ],
            "requests": list(self.requests.values()),
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
