"""The configuration's operations: read, write, detect, doctor, propose, and
``models.list``.

The Settings page uses all six; ``config.doctor`` and ``models.list`` are
open to agents too, because they only read. Writing is a person's: an agent that could write
.aegis.yaml could raise its own permission. A write goes to the file only; the
running server picks it up the way it picks up an editor's (config.py).
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, Field

from .config import AgentDoc, ConfigDoc, McpDoc, mcp_raw, stamp_from, write
from . import mcp_servers
from .ops import OpError
from . import service
from .doctor import Found, detect, doctor, propose
from .agents import agents_from
from .doctor_watch import DoctorWatch
from .ranking import ranking

if TYPE_CHECKING:
    from .app import App

DETECT_TTL_S = 60.0


class CatalogParams(BaseModel):
    provider: str | None = None
    free: bool | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class BrowseParams(BaseModel):
    harness: str = "opencode"
    providers: list[str] = Field(default_factory=list)
    price: Literal["all", "free", "paid", "unknown"] = "all"
    usable: bool = False
    search: str = ""
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class PreviewParams(BaseModel):
    harness: str = "opencode"
    models: list[str] | dict = Field(default_factory=list)


class SaveAgentParams(BaseModel):
    agent: AgentDoc
    stamp: str | None = None
    replace: bool = False


class McpTest(BaseModel):
    entry: McpDoc


class WriteParams(BaseModel):
    model_config = {"extra": "forbid"}
    doc: ConfigDoc
    stamp: str | None = None


def register_config_ops(app: App) -> None:
    r = app.registry
    cache: dict[str, tuple[float, list[Found]]] = {}

    def bins() -> dict[str, str]:
        return {
            "claude-code": app.claude_bin,
            "opencode": app.opencode_bin,
            "codex": app.codex_bin,
            "cursor": app.cursor_bin,
            "pi": "pi",
        }

    async def checked():
        # What `aegis doctor` prints: the config, the harnesses, the service.
        return await doctor(app.roots, bins(), extra_mcp=app.mcp) + service.check()

    app.doctor_watch = DoctorWatch(checked)

    async def detected() -> list[Found]:
        hit = cache.get("found")
        if hit is not None and time.monotonic() - hit[0] < DETECT_TTL_S:
            return hit[1]
        found = await detect(app.roots.config_root, bins())
        cache["found"] = (time.monotonic(), found)
        return found

    @r.op("config.read", access="read")
    async def config_read(_, caller):
        """.aegis.yaml as aegis holds it: the form's document, its stamp, and
        the error when the file on disk does not parse."""
        return app.config.current().wire()

    @r.op("config.write", WriteParams, server=True)
    async def config_write(p: WriteParams, caller):
        """Write the Settings form to .aegis.yaml. Refused when the file changed
        since `stamp`; nothing is written when the document has a problem."""
        problems = write(app.config.path, p.doc, stamp_from(p.stamp), app.mcp)
        return {
            "saved": not problems,
            "problems": [f.wire() for f in problems],
            "config": app.config.current().wire(),
        }

    @r.op("mcp.test", McpTest, server=True, local=True)
    async def mcp_test(p: McpTest, caller):
        """Connect to an MCP server as the Settings form holds it, saved or
        not, and name its tools. Runs a stdio server's command on this machine."""
        name = p.entry.name
        servers, errors = mcp_servers.parse({name: mcp_raw(p.entry)})
        if name in errors:
            raise OpError("mcp_test", f"{name}: {errors[name]}")
        s = mcp_servers.expand(servers[name], os.environ)
        try:
            return {"tools": await mcp_servers.tools_of(s)}
        except TimeoutError as e:
            raise OpError("mcp_test", f"{name}: no answer within 10 s") from e
        except Exception as e:  # whatever the server or its transport raised
            raise OpError("mcp_test", f"{name}: {str(e) or type(e).__name__}") from e

    @r.op("config.detect", server=True, access="read")
    async def config_detect(_, caller):
        """The harnesses installed on this machine: binary, version, models."""
        return [f.wire() for f in await detected()]

    @r.op("config.doctor", agent=True, read=True, access="read")
    async def config_doctor(_, caller):
        """Check .aegis.yaml, the harnesses it names and the state directory.
        Each finding has a level (ok, warn, error), where it is, and what is
        wrong."""
        return [f.wire() for f in await doctor(app.roots, bins(), extra_mcp=app.mcp)]

    @r.op("config.propose", server=True)
    async def config_propose(_, caller):
        """The first configuration `aegis init` would write here."""
        return propose(await detected()).model_dump()

    @r.op("models.catalog", CatalogParams, agent=True, read=True, access="read")
    async def models_catalog(p: CatalogParams, caller):
        """Public provider offers, scores and free status from the shared
        catalogue. These are advertised offers, not a claim that this
        machine has credentials or quota. Filter by provider or free status;
        paginate with offset and limit. Unknown free status is null."""
        rk = ranking()
        scored_ids = {s.aa for s in rk.models.values() if s.intelligence is not None}
        by_slug = {s.aa: s for s in rk.models.values()}
        ordered = sorted(
            scored_ids,
            key=lambda k: (by_slug[k].intelligence or 0, by_slug[k].coding or 0),
            reverse=True,
        )
        ranks = {slug: i + 1 for i, slug in enumerate(ordered)}
        rows = []
        for value, offer in rk.offers.items():
            if p.provider is not None and offer["provider"] != p.provider:
                continue
            if p.free is not None and offer["free"] is not p.free:
                continue
            score = by_slug.get(offer.get("aa") or "")
            rows.append(
                {
                    "value": value,
                    **offer,
                    "rank": ranks.get(offer.get("aa")),
                    "intelligence": score.intelligence if score else None,
                    "coding": score.coding if score else None,
                }
            )
        rows.sort(key=lambda m: (m["rank"] is None, m["rank"] or 0, m["value"]))
        end = p.offset + p.limit
        return {
            "refreshed": rk.refreshed,
            "catalog_refreshed": rk.catalog_refreshed,
            "source_url": rk.url,
            "catalog_source_url": "https://models.dev/api.json",
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
            "models": rows[p.offset : end],
        }

    async def local_models(harness):
        if harness == "opencode":
            await app.model_catalog.refresh()
            return (
                list(app.model_catalog.models.values())
                if app.model_catalog.fresh
                else []
            )
        return [
            m
            for f in await detected()
            if f.harness == harness and not f.error
            for m in f.models
        ]

    async def preview_models(p, caller):
        raw = {
            "harness": p.harness,
            "models": p.models,
            "effort": "high",
            "permission": "read",
        }
        (agent,) = agents_from({"agents": {"preview": raw}})
        if agent.error:
            raise OpError("bad_model_selection", agent.error)
        local = {m.value: m for m in await local_models(p.harness)}
        candidates = (
            app.model_catalog.candidates(agent.model_selector)
            if agent.model_selector
            else list(agent.models)
        )
        eligible = [
            m
            for m in candidates
            if m in local
            and local[m].can_agent
            and app.offers(caller, harness=p.harness, model=m)
        ]
        if not eligible or (
            not agent.model_selector and len(eligible) != len(candidates)
        ):
            raise OpError(
                "model_ineligible",
                app.model_catalog.error
                or "Selection contains no usable models, or a selected model is unavailable",
            )
        excluded = []
        if agent.model_selector:
            values = set(local) | set(ranking().offers)
            for value in sorted(values):
                if value.partition("/")[0] not in agent.model_selector["providers"]:
                    continue
                why = app.model_catalog.reason(agent.model_selector, value)
                if why:
                    excluded.append({"value": value, "reason": why})
        return {
            "models": [local[m].wire() for m in eligible],
            "excluded": excluded[:200],
            "excluded_total": len(excluded),
            "observed_at": app.model_catalog.observed_at,
            "refreshed": ranking().refreshed,
        }

    @r.op("models.preview", PreviewParams, agent=True, read=True, access="read")
    async def models_preview(p: PreviewParams, caller):
        """Resolve a fixed model list or a free-provider rule against local
        availability, prices and host policy, without saving or launching."""
        return await preview_models(p, caller)

    @r.op("models.save", SaveAgentParams, server=True)
    async def models_save(p: SaveAgentParams, caller):
        """Create or edit one agent after validating its model selection;
        save through the existing configuration writer and stale-write check."""
        a = p.agent
        if isinstance(a.models, list) and len(a.models) == 1:
            a = a.model_copy(update={"model": a.models[0], "models": []})
        selection = a.models or ([a.model] if a.model else [])
        await preview_models(PreviewParams(harness=a.harness, models=selection), caller)
        doc = app.config.current().doc.model_copy(deep=True)
        old = next(
            (i for i, agent in enumerate(doc.agents) if agent.name == a.name), None
        )
        if old is not None and not p.replace:
            raise OpError(
                "agent_exists",
                "An agent with this name already exists; choose Edit agent",
            )
        if p.replace and old is None:
            raise OpError("unknown_agent", "The agent being edited no longer exists")
        if old is None:
            doc.agents.append(a)
        else:
            doc.agents[old] = a
        problems = write(app.config.path, doc, stamp_from(p.stamp), app.mcp)
        return {
            "saved": not problems,
            "problems": [f.wire() for f in problems],
            "config": app.config.current().wire(),
        }

    @r.op("models.browse", BrowseParams, server=True, access="read")
    async def models_browse(p: BrowseParams, caller):
        """Public provider offers joined with this harness's local usability,
        with server-side filters, stable paging and exclusion reasons."""
        local = {m.value: m for m in await local_models(p.harness)}
        rk = ranking()
        rows = []
        provider_names = {}
        values = set(rk.offers) | set(local) if p.harness == "opencode" else set(local)
        by_slug = {score.aa: score for score in rk.models.values()}
        ordered = sorted(
            (s for s in by_slug.values() if s.intelligence is not None),
            key=lambda s: (
                -(s.intelligence or 0),
                -(s.coding if s.coding is not None else -1),
                s.aa,
            ),
        )
        ranks = {s.aa: i + 1 for i, s in enumerate(ordered)}
        for value in sorted(values):
            if not app.offers(caller, harness=p.harness, model=value):
                continue
            offer = rk.offers.get(value, {})
            m = local.get(value)
            provider = value.partition("/")[0] if "/" in value else p.harness
            provider_names[provider] = offer.get("provider_name", provider)
            free = (
                (
                    (m.price_in == 0 and m.price_out == 0)
                    if m.price_in is not None and m.price_out is not None
                    else None
                )
                if m
                else offer.get("free")
            )
            why = (
                app.model_catalog.reason({"providers": [provider], "free": True}, value)
                if p.harness == "opencode"
                else None
            )
            score = rk.score(value)
            rows.append(
                {
                    "value": value,
                    "provider": provider,
                    "label": m.label if m else offer.get("label", value),
                    "free": free,
                    "price_in": m.price_in if m else offer.get("price_in"),
                    "price_out": m.price_out if m else offer.get("price_out"),
                    "intelligence": score.intelligence if score else None,
                    "coding": score.coding if score else None,
                    "rank": ranks.get(score.aa) if score else None,
                    "available": m is not None and m.can_agent,
                    "can_agent": m.can_agent if m else offer.get("tool_call"),
                    "eligible": m is not None and why is None,
                    "reason": (
                        "This model does not support agent tool calls and text output"
                        if m is not None and not m.can_agent
                        else why
                    )
                    or ("Connect this provider to use it" if m is None else ""),
                    "window": m.window if m else None,
                    "source_url": offer.get("source_url", rk.url),
                    "price_source": "local harness" if m else "models.dev",
                }
            )
        query = p.search.strip().lower()
        rows = [
            m
            for m in rows
            if (not p.providers or m["provider"] in p.providers)
            and (not p.usable or m["available"])
            and (not query or query in (m["value"] + " " + m["label"]).lower())
            and (
                p.price == "all"
                or p.price == "free"
                and m["free"] is True
                or p.price == "paid"
                and m["free"] is False
                or p.price == "unknown"
                and m["free"] is None
            )
        ]
        rows.sort(key=lambda m: (m["rank"] is None, m["rank"] or 0, m["value"]))
        end = p.offset + p.limit
        return {
            "models": rows[p.offset : end],
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
            "providers": [
                {"id": k, "name": v} for k, v in sorted(provider_names.items())
            ],
            "refreshed": rk.refreshed,
            "catalog_refreshed": rk.catalog_refreshed,
            "observed_at": app.model_catalog.observed_at,
            "local_error": app.model_catalog.error if p.harness == "opencode" else "",
        }

    @r.op("models.list", agent=True, read=True, access="read")
    async def models_list(_, caller):
        """Every model the installed harnesses can run, with its rank among
        its harness's models, Artificial Analysis's Coding and Intelligence
        Indexes, its price in and out per million tokens, the agents that
        name it (at: its index in the agent's models, 0 first) and the epoch
        it stops cooling, or null. Models in use come first, then by
        Intelligence Index, the Coding Index breaking ties; those with no
        Intelligence Index last."""
        agents = app.config.current().agents
        now = time.time()
        rows = []
        for f in await detected():
            for m in f.models:
                if not app.offers(caller, harness=f.harness, model=m.value):
                    continue
                used = [
                    {"agent": a.name, "at": a.models.index(v)}
                    for a in agents
                    if a.harness == f.harness
                    for v in (m.value, m.resolved)
                    if v in a.models
                ]
                rows.append(
                    {
                        "harness": f.harness,
                        **m.wire(),
                        "used_by": list({u["agent"]: u for u in used}.values()),
                        "cooling_until": app.cooling.until(f.harness, m.value, now),
                    }
                )
        rows.sort(
            key=lambda x: (
                not x["used_by"],
                x["intelligence"] is None,
                -(x["intelligence"] or 0),
                -(x["coding"] or 0) if x["intelligence"] is not None else 0,
            )
        )
        rk = ranking()
        return {"refreshed": rk.refreshed, "source_url": rk.url, "models": rows}
