"""A private, session-bound Chrome bridge; no model-supplied JavaScript."""

from __future__ import annotations

import asyncio
from typing import Any

from ..chrome import CallError, Chrome
from .providers import PROVIDERS, STOP_SCRIPT


class WebBridges:
    def __init__(self, chrome: Chrome) -> None:
        self.chrome = chrome
        self.bridges: dict[str, BrowserBridge] = {}
        self.leases: dict[str, str] = {}
        self.submissions = asyncio.Lock()

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
            f"Start this session's {self.provider.label} conversation.",
        )
        self.request_id = req["id"]
        try:
            # Selecting this harness authorizes its own new provider tab.
            # Use the existing extension protocol so the grant remains owned
            # by Chrome and can still be paused or revoked by the person.
            await self.chrome.answer(req["id"], True)
            tab = await self.waiting
            claimed = self._claim(tab)
            await self.chrome.scripts(claimed["tab"], True)
            return claimed
        finally:
            self.waiting = None

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
        if op == "state":
            self.session.record_card(
                {
                    "kind": "web_connection",
                    "state": args["state"],
                    "text": args["text"],
                    "url": args.get("url", ""),
                    "tab": self.tab,
                }
            )
            return {}
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
                return await self._eval(self.provider.observe_script)
            if op == "stop":
                return await self._eval(STOP_SCRIPT)
            # The observer has prepared the page for background execution.
            # Keep each short input transaction ordered across web sessions.
            async with self.manager.submissions:
                self._check(args)
                await self.chrome.call(
                    self.session.log_id,
                    self.tab,
                    "type",
                    {
                        "target": self.provider.input,
                        "text": args["text"],
                        "submit": False,
                    },
                )
                if script := self.provider.send_ready_script:
                    if self.provider.name == "web-qwen":
                        # Qwen suppresses Enter after compositionend. Background
                        # tabs can defer its 100 ms release timer to one second.
                        await asyncio.sleep(1.1)
                    last_rect = None
                    for attempt in range(21):
                        self._check(args)
                        submitted = await self._eval(script)
                        rect = submitted.get("rect")
                        if (
                            submitted.get("ready") is True
                            and rect is not None
                            and rect == last_rect
                        ):
                            if self.provider.send_key:
                                await self.chrome.call(
                                    self.session.log_id,
                                    self.tab,
                                    "press",
                                    {
                                        "target": self.provider.input,
                                        "key": self.provider.send_key,
                                    },
                                )
                            else:
                                await self.chrome.call(
                                    self.session.log_id,
                                    self.tab,
                                    "click",
                                    {"target": self.provider.send},
                                )
                            break
                        last_rect = rect if submitted.get("ready") is True else None
                        if attempt == 20:
                            raise CallError(
                                "submission_refused",
                                f"{self.provider.label} did not enable its Send button; check the draft in Chrome",
                            )
                        await asyncio.sleep(0.1)
                else:
                    self._check(args)
                    await self.chrome.call(
                        self.session.log_id,
                        self.tab,
                        "press",
                        {"target": self.provider.input, "key": "Enter"},
                    )
                self.chrome.acted(self.tab, self.session.handle, "web harness submit")
                return {"submitted": True}

    async def _eval(self, expression: str) -> dict:
        assert self.tab is not None
        reply = await self.chrome.call(
            self.session.log_id,
            self.tab,
            "eval",
            {"expression": expression, "web": True},
        )
        if not isinstance(reply, dict) or reply.get("web_ready") is not True:
            raise CallError(
                "extension_update_required",
                "Update and reload Chrome extension 0.5.3 to run web sessions in background tabs",
            )
        value = reply.get("value")
        if not isinstance(value, dict):
            raise CallError(
                "unsupported_layout",
                "The Chrome observer returned an unsupported result",
            )
        return value
