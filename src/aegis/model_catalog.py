"""Join public offers with local OpenCode discovery; enforce free-only selectors.

Metadata is shared by the browser preview, launch gates and session turns.
A failed refresh never renews the age of an old zero-price observation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from typing import TYPE_CHECKING

from .doctor import detect
from .ops import OpError
from .ranking import ranking

if TYPE_CHECKING:
    from .app import App

MAX_AGE_S = 60.0


class ModelCatalog:
    def __init__(self, app: App):
        self.app = app
        self.models = {}
        self.observed_at = 0.0
        self.error = "Local model metadata has not been read yet"
        self._fetch = None
        self._loop = None

    @property
    def fresh(self) -> bool:
        return bool(self.observed_at and time.time() - self.observed_at < MAX_AGE_S)

    async def _read(self) -> None:
        try:
            found = await detect(
                self.app.roots.config_root,
                {
                    "claude-code": self.app.claude_bin,
                    "opencode": self.app.opencode_bin,
                    "codex": self.app.codex_bin,
                    "cursor": self.app.cursor_bin,
                },
                ("opencode",),
            )
            f = next((f for f in found if f.harness == "opencode"), None)
            if f is None or f.error:
                self.error = (
                    f.error if f is not None else "OpenCode gave no model catalogue"
                )
                return
            self.models = {m.value: m for m in f.models}
            self.observed_at = time.time()
            self.error = ""
        except Exception as e:
            self.error = str(e) or type(e).__name__

    async def refresh(self) -> None:
        if self.fresh:
            return
        if self._fetch is None or self._fetch.done():
            self._fetch = asyncio.create_task(self._read())
        await asyncio.shield(self._fetch)

    def request(self) -> None:
        if not self.fresh and (self._fetch is None or self._fetch.done()):
            self._fetch = asyncio.create_task(self._read())

    def reason(self, selector: dict, value: str) -> str | None:
        if not self.fresh:
            return (
                self.error
                or "Local model metadata is stale; refresh before using a free-only agent"
            )
        if value.partition("/")[0] not in selector["providers"]:
            return f"{value}: provider is outside this agent's rule"
        m = self.models.get(value)
        if m is None:
            return f"{value}: not available through local OpenCode; connect the provider first"
        if (
            not m.free
            or m.price_in != 0
            or m.price_out != 0
            or m.price_source == "Artificial Analysis list price"
        ):
            return f"{value}: zero input/output prices and tool support are required"
        offer = ranking().offers.get(value)
        if offer and any((offer.get(k) or 0) > 0 for k in ("price_in", "price_out")):
            return f"{value}: public and local prices conflict"
        return None

    def permits(self, spec, model: str) -> bool:
        who = self.app.whos.get(spec.owner or self.app.user)
        models = self.app.policy.catalog(who).models
        return models is None or model in models

    def restriction(self, spec, model: str) -> str | None:
        why = self.reason(spec.model_selector, model)
        if why:
            return why
        return (
            None
            if self.permits(spec, model)
            else "Model is not offered by the host policy"
        )

    def candidates(self, selector: dict) -> list[str]:
        if not self.fresh:
            self.request()
            return []
        return ranking().order(
            sorted(m for m in self.models if self.reason(selector, m) is None)
        )

    async def prepare_turn(self, session) -> None:
        await self.refresh()
        spec = session.spec
        await self.app.who_of_key(spec.owner or self.app.user)
        if not spec.model_selector:
            return
        if not self.restriction(spec, spec.model):
            return
        if not self.fresh:
            raise OpError(
                "model_ineligible",
                self.restriction(spec, spec.model) or "Model metadata is stale",
            )
        cascade = spec.cascade
        at = cascade.index(spec.model) if spec.model in cascade else -1
        for model in cascade[at + 1 :]:
            if (
                self.restriction(spec, model) is None
                and self.app.gate(spec.owner, spec.harness, model).may_start
                and self.app.cooling.until(spec.harness, model, time.time()) is None
            ):
                await session.configure(model=model)
                session.spec = dataclasses.replace(
                    session.spec, models_observed_at=self.observed_at
                )
                return
        raise OpError(
            "model_ineligible",
            "No eligible free model remains in this session's cascade",
        )

    async def _run(self) -> None:
        while True:
            agents = self.app.config.current().agents
            sessions = self.app.sessions.sessions.values()
            if any(a.model_selector for a in agents) or any(
                s.spec.model_selector for s in sessions
            ):
                await self.refresh()
                self.app._release_held()
            await asyncio.sleep(30)

    def start(self) -> None:
        self._loop = asyncio.create_task(self._run())

    async def stop(self) -> None:
        tasks = [t for t in (self._loop, self._fetch) if t is not None]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
