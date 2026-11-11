"""The operations behind Settings → Chrome and the person's shared tabs
(chrome.py): a pairing code, unpairing, the theme, answering an agent's
request and revoking a grant; and the agents' browser_list and
browser_request. browser_open(where="person") reaches ``Chrome.ask_for``, and
the browser_* tools on a ``chrome:`` id reach ``Chrome.drive``."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from .browsers import SNAPSHOT_MAX, event_line
from .chrome import CallError, Chrome, PairError
from .monitors import iso_now
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger(__name__)


class ChromeDevice(BaseModel):
    id: str


class ChromeTheme(BaseModel):
    vars: dict[str, str]


class ChromeAnswer(BaseModel):
    request: str
    give: bool


class ChromeTab(BaseModel):
    tab: str


class ChromeRevoke(BaseModel):
    tab: str
    log_id: str | None = None


class BrowserRequest(BaseModel):
    match: str = Field(
        min_length=1,
        max_length=200,
        description="What tab you want, in words or part of its address:"
        " qwen.ai, or the pull request I have open.",
    )
    why: str = Field(
        "", max_length=300, description="Why you need it, shown to the person."
    )


def register_chrome_ops(app: App) -> Chrome:
    r = app.registry
    chrome = Chrome(app.roots.state_root, app.publish)

    def sessions() -> list[dict]:
        return [
            {"log_id": s.log_id, "handle": s.handle, "title": s.title or ""}
            for s in app.sessions.open_sessions()
            if not s.worker
        ]

    def wake(log_id: str, header: str, body: str) -> None:
        s = app.sessions.sessions.get(log_id)
        if s is None:
            return

        async def go() -> None:
            try:
                await s.deliver(header, body)
            except Exception:  # an archived or locked session: its card says it
                log.info("chrome answer with no session to wake: %s", log_id)

        asyncio.get_running_loop().create_task(go())

    def answered(req: dict, outcome: str, tab: dict | None) -> None:
        s = app.sessions.sessions.get(req["log_id"])
        rec = {"kind": "chrome_answer", "request_id": req["id"], "answer": outcome}
        if tab is not None:
            rec |= {"tab": tab["name"], "title": tab["title"], "url": tab["url"]}
        if s is not None:
            s.record_card(rec)
        asked = req["url"] if req["kind"] == "open" else req["match"]
        if outcome == "granted" and tab is not None:
            wake(
                req["log_id"],
                f"> from chrome:{tab['name']} · granted · {iso_now()}",
                f"The person gave you chrome:{tab['name']}, {tab['title'] or 'a tab'}"
                f" at {tab['url']}. browser_list shows your tabs.",
            )
        else:
            wake(
                req["log_id"],
                f"> from chrome · denied · {iso_now()}",
                f"The person refused your request for {asked}.",
            )

    def back(woken: list[str], tab: dict, evs: list[dict]) -> None:
        lines = [
            f"The person gave chrome:{tab['name']} back.",
            f"Page: {tab['url']}  {tab['title']}".rstrip(),
        ]
        if evs := [e for e in evs if e["kind"] != "control"]:
            lines += ["What happened meanwhile:"] + [event_line(e) for e in evs[-60:]]
        lines.append(
            "Read it with browser_snapshot; browser_events gives the full log."
        )
        for log_id in woken:
            wake(
                log_id,
                f"> from chrome:{tab['name']} · back · {iso_now()}",
                "\n".join(lines),
            )

    chrome.on_back = back
    chrome.list_sessions = sessions
    chrome.on_answer = answered
    chrome.on_nomatch = lambda req: app.toast(
        f"No tab of your browser matches \u201c{req['match']}\u201d: pick one in"
        " the extension's popup, or refuse."
    )

    async def ask(caller: Caller, kind: str, match: str, url: str, why: str) -> dict:
        s = app.sessions.sessions.get(caller.log_id or "")
        if not caller.is_agent or s is None:
            raise OpError("agents_only", "only an agent's session can ask for a tab")
        if not chrome.connected_any():
            raise OpError(
                "chrome_unavailable",
                "the person's browser is not connected to aegis; wait for it or"
                " use a server browser",
            )
        req = await chrome.ask(s.log_id, s.handle, kind, match, url, why)
        s.record_card(
            {
                "kind": "chrome_request",
                "request_id": req["id"],
                "req_kind": kind,
                "match": match,
                "url": url,
                "why": why,
            }
        )
        return {"request": req["id"], "state": "asked"}

    chrome.ask_for = ask

    # What the extension does on a person's tab; the rest wait for later slices.
    reads = ("snapshot", "screenshot", "events")
    acts = ("click", "type", "press", "goto", "handover")

    async def drive(caller: Caller, op: str, p) -> object:
        """A browser_* tool on chrome:<name>, run by the person's browser."""
        name = (p.browser or "").removeprefix("chrome:")
        s = app.sessions.sessions.get(caller.log_id or "")
        if s is None:
            raise OpError("agents_only", "only an agent's session drives a tab")
        if op not in reads + acts:
            raise OpError(
                "not_supported",
                f"browser_{op} does not work on the person's tabs yet",
            )
        try:
            chrome.tab_for(s.log_id, name)
            if op == "events":
                rev, evs = chrome.events(name, p.since)
                if p.who != "all":
                    evs = [e for e in evs if e.get("who") == p.who]
                return {"rev": rev, "events": [event_line(e) for e in evs[-200:]]}
            args = p.model_dump(exclude={"browser"})
            if op in reads:
                rev, _ = chrome.events(name)
                value = await chrome.call(s.log_id, name, op, args)
            else:
                # Actions on one tab wait for each other, in order of arrival.
                async with chrome.lock(name):
                    tab = chrome.tab_for(s.log_id, name)
                    if tab.get("holder") == "person":
                        chrome.refused(name, s.log_id)
                        ask = f" ({tab['ask']})" if tab.get("ask") else ""
                        raise OpError(
                            "person_has_control",
                            f"the person holds chrome:{name}{ask}; end your turn,"
                            " you are woken when they give it back",
                        )
                    if op == "handover":
                        await chrome.hold(name, "person", p.reason, s.log_id, s.handle)
                        app.toast(
                            f"{s.handle} needs you in your browser, on"
                            f" {tab['title'] or name}: {p.reason}"
                        )
                        return (
                            f"The person holds chrome:{name} now and sees: {p.reason}."
                            " End your turn; you are woken when they give it back."
                        )
                    rev, _ = chrome.events(name)
                    value = await chrome.call(s.log_id, name, op, args)
        except CallError as e:
            raise OpError(e.code, e.message) from None
        if op == "snapshot":
            tree = value.get("tree", "")
            if len(tree) > SNAPSHOT_MAX:
                tree = tree[:SNAPSHOT_MAX] + "\n… (cut)"
            return f"{value.get('url', '')}  {value.get('title', '')}\nholder: agent\n\n{tree}"
        if op == "screenshot":
            shots = chrome.shots_dir(name)
            shots.mkdir(parents=True, exist_ok=True)
            path = shots / f"{time.strftime('%Y%m%d-%H%M%S')}-{rev}.png"
            path.write_bytes(base64.b64decode(value.get("png", "")))
            return {"path": str(path)}
        if op == "goto":
            line = f"goto {p.url or p.action}"
        elif op == "type":
            shown = (
                f"{len(p.text)} characters"
                if value.get("secret")
                else json.dumps(p.text[:80], ensure_ascii=False)
            )
            line = f"type {shown} into {p.target}"
        elif op == "press":
            line = f"press {p.key}"
        else:
            line = f"click {p.target}"
        chrome.acted(name, s.handle, line)
        _, evs = chrome.events(name, rev)
        out = {
            "url": value.get("url", ""),
            "title": value.get("title", ""),
            "events": [event_line(e) for e in evs if e["kind"] != "agent"][-30:],
        }
        if value.get("note"):
            out["note"] = value["note"]
        return out

    chrome.drive = drive

    # -- the person ---------------------------------------------------------------

    @r.op("chrome.code", server=True)
    async def code(_, caller):
        """A one-time code the extension's popup trades for its device token,
        valid ten minutes."""
        return chrome.new_code()

    @r.op("chrome.unpair", ChromeDevice, server=True)
    async def unpair(p: ChromeDevice, caller):
        """Forget a paired browser: its token stops working and its socket
        closes."""
        await chrome.unpair(p.id)

    @r.op("chrome.theme", ChromeTheme)
    async def theme(p: ChromeTheme, caller):
        """The CSS variables of the theme the person picked, for the
        extension's popup."""
        await chrome.set_theme(p.vars)

    @r.op("chrome.answer", ChromeAnswer)
    async def answer(p: ChromeAnswer, caller):
        """Give or refuse an agent's request for a tab, from its card."""
        if caller.is_agent:
            raise OpError("people_only", "only the person answers a request")
        try:
            await chrome.answer(p.request, p.give)
        except PairError as e:
            raise OpError(e.code, e.message) from e

    @r.op("chrome.release", ChromeTab)
    async def release(p: ChromeTab, caller):
        """Give a tab the person holds back to its agents: Done after a
        handover, Resume after a pause."""
        if caller.is_agent:
            raise OpError("people_only", "only the person gives a tab back")
        await chrome.hold(p.tab, "agent")

    @r.op("chrome.revoke", ChromeRevoke, names=(("log_id", "session"),))
    async def revoke(p: ChromeRevoke, caller):
        """Disconnect an agent from a shared tab, or with no log_id stop
        sharing the tab."""
        if caller.is_agent:
            raise OpError("people_only", "only the person ends a grant")
        await chrome.revoke(p.tab, p.log_id)

    # -- agents -------------------------------------------------------------------

    @r.op("browser.list", agent=True, read=True, access="read")
    async def browser_list(_, caller):
        """Every browser you may use: the server's (kind server) and the tabs
        of the person's own browser they gave you (kind person, id
        chrome:<name>)."""
        out = [
            {
                "id": b.id,
                "kind": "server",
                "title": b.meta.get("title") or "",
                "url": b.meta["url"],
                "state": b.meta["state"],
            }
            for b in app.browsers.open.values()
        ]
        if caller.log_id:
            out += [
                {
                    "id": f"chrome:{t['name']}",
                    "kind": "person",
                    "title": t["title"],
                    "url": t["url"],
                }
                for t in chrome.granted(caller.log_id)
            ]
        return out

    @r.op("browser.request", BrowserRequest, agent=True)
    async def browser_request(p: BrowserRequest, caller):
        """Ask the person for one of the tabs open in their own browser, with
        their sign-ins. Say what you want in match and why. End your turn: you
        are woken with granted, naming the tab's id, or denied. You never learn
        which other tabs exist."""
        return await ask(caller, "tab", p.match, "", p.why)

    return chrome
