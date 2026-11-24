"""A private, session-bound Chrome bridge; no model-supplied JavaScript."""

from __future__ import annotations

import asyncio
from typing import Any

from ..chrome import CallError, Chrome
from .providers import DEEPSEEK_SEND, PROVIDERS, STOP_SCRIPT


class WebBridges:
    def __init__(self, chrome: Chrome) -> None:
        self.chrome = chrome
        self.bridges: dict[str, BrowserBridge] = {}
        self.leases: dict[str, str] = {}

    def for_session(self, session: Any):
        provider = PROVIDERS.get(session.spec.harness)
        if provider is None:
            return None
        bridge = BrowserBridge(self, session, provider)
        self.bridges[session.log_id] = bridge
        return bridge.call

    def handles(self, log_id: str) -> bool:
        return log_id in self.bridges

    def answered(self, req: dict, outcome: str, tab: dict | None = None) -> bool:
        bridge = self.bridges.get(req["log_id"])
        if bridge is None or bridge.waiting is None or bridge.waiting.done():
            return False
        if bridge.request_id is not None and bridge.request_id != req["id"]:
            return False
        if outcome != "granted" or tab is None:
            bridge.waiting.set_exception(
                CallError("denied", "The backing Chrome tab was refused")
            )
        else:
            bridge.waiting.set_result(dict(tab))
        return True


class BrowserBridge:
    def __init__(self, manager: WebBridges, session: Any, provider) -> None:
        self.manager, self.chrome, self.session, self.provider = (
            manager,
            manager.chrome,
            session,
            provider,
        )
        self.tab: str | None = None
        self.waiting: asyncio.Future | None = None
        self.request_id: str | None = None

    async def _start(self, args: dict) -> dict:
        saved = args.get("conversation") or {}
        url = saved.get("url") or self.provider.url
        if not self.provider.accepts(url):
            raise CallError(
                "wrong_provider", "The saved conversation belongs to another provider"
            )
        if saved:
            for tab in self.chrome.granted(self.session.log_id):
                if tab["url"] == url and (
                    not saved.get("device") or tab["device"] == saved["device"]
                ):
                    return self._claim(tab)
        if not self.chrome.connected_any():
            raise CallError(
                "chrome_unavailable", "Connect Chrome to start the web harness"
            )
        self.waiting = asyncio.get_running_loop().create_future()
        req = await self.chrome.ask(
            self.session.log_id,
            self.session.handle,
            "open",
            "",
            url,
            f"Open this session's {self.provider.label} conversation. Enable Scripts for the shared tab.",
        )
        self.request_id = req["id"]
        self.session.record_card(
            {
                "kind": "chrome_request",
                "request_id": req["id"],
                "req_kind": "open",
                "match": "",
                "url": url,
                "why": req["why"],
            }
        )
        try:
            tab = await self.waiting
        finally:
            self.waiting = None
        return self._claim(tab)

    def _claim(self, tab: dict) -> dict:
        name = tab["name"]
        current = self.chrome.tab_for(self.session.log_id, name)
        if not self.provider.accepts(current["url"]):
            raise CallError(
                "wrong_provider", "The granted tab is not this session's provider"
            )
        if (owner := self.manager.leases.get(name)) and owner != self.session.log_id:
            raise CallError(
                "tab_in_use", "Another web session already owns this backing tab"
            )
        self.manager.leases[name] = self.session.log_id
        self.tab = name
        return {"tab": name, "url": current["url"], "device": current["device"]}

    def _check(self, args: dict) -> dict:
        if self.tab is None:
            raise CallError("not_granted", "The web session has no granted backing tab")
        tab = self.chrome.tab_for(self.session.log_id, self.tab)
        if tab.get("holder") == "person":
            raise CallError(
                "person_has_control", "The person holds the backing Chrome tab"
            )
        if not tab.get("eval"):
            raise CallError(
                "scripts_off",
                "Enable Scripts for this tab in the Chrome sharing controls",
            )
        if not self.provider.accepts(tab["url"]):
            raise CallError(
                "conversation_changed",
                "The backing tab navigated away from its provider",
            )
        if expected := args.get("url"):
            if self.provider.conversation(expected) and self.provider.conversation(
                tab["url"]
            ) != self.provider.conversation(expected):
                raise CallError(
                    "conversation_changed",
                    "The backing tab is showing a different conversation",
                )
        return tab

    async def call(self, op: str, args: dict) -> dict:
        if op == "start":
            return await self._start(args)
        if op == "release":
            if self.tab and self.manager.leases.get(self.tab) == self.session.log_id:
                self.manager.leases.pop(self.tab)
            if self.waiting and not self.waiting.done():
                self.waiting.cancel()
            if self.request_id in self.chrome.requests:
                self.chrome.requests.pop(self.request_id)
                await self.chrome._send(
                    None, {"t": "cancel", "request": self.request_id}
                )
                self.chrome.changed()
            if self.manager.bridges.get(self.session.log_id) is self:
                self.manager.bridges.pop(self.session.log_id)
            return {}
        if op not in ("observe", "send", "stop", "check"):
            raise ValueError(f"Unsupported web bridge operation {op!r}")
        self._check(args)
        assert self.tab is not None
        async with self.chrome.lock(self.tab):
            self._check(args)
            if op == "check":
                return {}
            if op == "observe":
                return await self.chrome.call(
                    self.session.log_id,
                    self.tab,
                    "eval",
                    {"expression": self.provider.observe_script},
                )
            if op == "stop":
                return await self.chrome.call(
                    self.session.log_id, self.tab, "eval", {"expression": STOP_SCRIPT}
                )
            await self.chrome.call(
                self.session.log_id,
                self.tab,
                "type",
                {
                    "target": self.provider.input,
                    "text": args["text"],
                    "submit": self.provider.name != "web-deepseek",
                },
            )
            if self.provider.name == "web-deepseek":
                await self.chrome.call(
                    self.session.log_id, self.tab, "eval", {"expression": DEEPSEEK_SEND}
                )
            self.chrome.acted(self.tab, self.session.handle, "web harness submit")
            return {"submitted": True}
