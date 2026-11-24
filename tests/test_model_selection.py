"""Free-provider rules survive configuration and are enforced at every use."""

import dataclasses
import time

import pytest

from aegis.agents import agents_from, resolve
from aegis.app import App
from aegis.claude.control import Model
from aegis.config import doc_from, write
from aegis.ops import Caller, OpError
from aegis.ranking import Ranking
from aegis.roots import make_roots
from aegis.session import SpawnSpec

RULE = {"providers": ["one", "two"], "free": True}
RAW = {
    "agents": {
        "free": {
            "harness": "opencode",
            "models": RULE,
            "effort": "high",
            "permission": "read",
        }
    }
}


def model(value, price=0):
    return Model(
        value, value, value, "", (), free=price == 0, price_in=price, price_out=price
    )


@pytest.fixture
def app(tmp_path, monkeypatch):
    import aegis.config_ops as ops
    import aegis.model_catalog as catalog
    from ruamel.yaml import YAML

    YAML().dump(RAW, tmp_path / ".aegis.yaml")
    app = App(make_roots(tmp_path, tmp_path))
    app.model_catalog.models = {
        m.value: m
        for m in (
            model("one/a"),
            model("two/b"),
            model("one/paid", 1),
            model("one/unknown", None),
        )
    }
    app.model_catalog.observed_at = time.time()
    app.model_catalog.error = ""
    rk = Ranking.from_dict(
        {
            "refreshed": "2026-11-24",
            "models": {
                "one/a": {"aa": "a", "intelligence": 50},
                "two/b": {"aa": "b", "intelligence": 60},
            },
            "offers": {
                "remote/free": {
                    "provider": "remote",
                    "free": True,
                    "price_in": 0,
                    "price_out": 0,
                }
            },
        }
    )
    monkeypatch.setattr(ops, "ranking", lambda: rk)
    monkeypatch.setattr(catalog, "ranking", lambda: rk)
    return app


def test_rule_parses_and_roundtrips_without_becoming_an_empty_model(tmp_path):
    (a,) = agents_from(RAW)
    assert a.enabled and not a.model and a.model_selector == RULE
    spec = resolve([a], "free", None, {}, tmp_path)
    assert spec.model_selector == RULE
    assert SpawnSpec.from_record(spec.record(), tmp_path).model_selector == RULE
    path = tmp_path / ".aegis.yaml"
    assert not write(path, doc_from(RAW), None)
    from aegis.agents import load_agents

    (loaded,) = load_agents(tmp_path)
    assert loaded.model_selector == RULE and loaded.enabled


@pytest.mark.parametrize(
    "rule",
    [
        {},
        {"providers": [], "free": True},
        {"providers": ["one"], "free": False},
        {"providers": ["one"], "free": True, "typo": 1},
    ],
)
def test_invalid_rules_are_disabled(rule):
    raw = {"agents": {"a": {**RAW["agents"]["free"], "models": rule}}}
    (a,) = agents_from(raw)
    assert not a.enabled


def test_launch_ranks_rule_and_refuses_paid_override(app):
    (a,) = app.config.current().agents
    spec = resolve([a], a.name, None, {}, app.roots.config_root)
    selected, gate = app.start_spec(spec)
    assert gate.may_start and selected.model == "two/b"
    assert selected.cascade == ("two/b", "one/a")
    own = dataclasses.replace(spec, model="one/paid")
    _, gate = app.start_spec(own)
    assert not gate.may_start


async def test_price_change_skips_to_next_frozen_model(app):
    spec = SpawnSpec(
        "free",
        "two/b",
        "high",
        "read",
        app.roots.config_root,
        harness="opencode",
        cascade=("two/b", "one/a"),
        model_selector=RULE,
    )

    class Session:
        def __init__(self):
            self.spec = spec

        async def configure(self, model):
            self.spec = dataclasses.replace(self.spec, model=model)

    s = Session()
    app.model_catalog.models["two/b"] = model("two/b", 1)
    app.model_catalog.models["one/new"] = model("one/new")
    await app.model_catalog.prepare_turn(s)
    assert s.spec.model == "one/a" and "one/new" not in s.spec.cascade
    app.model_catalog.models["one/a"] = model("one/a", None)
    with pytest.raises(OpError, match="No eligible free model"):
        await app.model_catalog.prepare_turn(s)


async def test_stale_metadata_is_not_used_after_failed_refresh(app, monkeypatch):
    import aegis.model_catalog as module

    app.model_catalog.observed_at = time.time() - 120
    old = app.model_catalog.observed_at

    async def broken(*args):
        raise RuntimeError("offline")

    monkeypatch.setattr(module, "detect", broken)
    await app.model_catalog.refresh()
    assert app.model_catalog.observed_at == old and not app.model_catalog.fresh
    assert "offline" in app.model_catalog.reason(RULE, "one/a")


async def test_preview_filters_save_and_stale_write(app):
    preview = await app.registry.call("models.preview", {"models": RULE})
    assert [m["value"] for m in preview["models"]] == ["two/b", "one/a"]
    assert preview["excluded_total"] == 2
    browse = await app.registry.call("models.browse", {"price": "free", "limit": 1})
    assert browse["total"] == 3 and browse["next_offset"] == 1
    assert browse["models"][0]["value"] == "two/b"
    config = app.config.current().wire()
    payload = {
        "agent": {
            "name": "new",
            "harness": "opencode",
            "models": RULE,
            "effort": "high",
            "permission": "read",
        },
        "stamp": config["stamp"],
    }
    saved = await app.registry.call("models.save", payload)
    assert saved["saved"]
    assert (
        next(a for a in saved["config"]["doc"]["agents"] if a["name"] == "new")[
            "models"
        ]
        == RULE
    )
    payload["agent"]["name"] = "stale"
    with pytest.raises(OpError, match="stale"):
        await app.registry.call("models.save", payload)
    assert "stale" not in [a.name for a in app.config.current().agents]
    with pytest.raises(OpError):
        await app.registry.call("models.save", payload, Caller("agent", log_id="x"))


async def test_single_fixed_selection_saves_a_real_model(app):
    payload = {
        "agent": {
            "name": "single",
            "harness": "opencode",
            "models": ["one/a"],
            "effort": "high",
            "permission": "read",
        },
        "stamp": app.config.current().wire()["stamp"],
    }
    assert (await app.registry.call("models.save", payload))["saved"]
    a = next(a for a in app.config.current().agents if a.name == "single")
    assert a.model == "one/a" and a.enabled


async def test_zero_price_without_agent_capability_is_visible_but_not_selectable(app):
    app.model_catalog.models["one/video"] = dataclasses.replace(
        model("one/video"), free=False, can_agent=False
    )
    result = await app.registry.call("models.browse", {"price": "free"})
    row = next(m for m in result["models"] if m["value"] == "one/video")
    assert row["free"] is True and row["available"] is False
    with pytest.raises(OpError, match="unavailable"):
        await app.registry.call("models.preview", {"models": ["one/video"]})


async def test_public_positive_price_excludes_a_locally_free_offer(app, monkeypatch):
    import aegis.config_ops as ops
    import aegis.model_catalog as module

    rk = dataclasses.replace(
        ops.ranking(),
        offers={
            "one/a": {"provider": "one", "free": False, "price_in": 1, "price_out": 2}
        },
    )
    monkeypatch.setattr(ops, "ranking", lambda: rk)
    monkeypatch.setattr(module, "ranking", lambda: rk)
    assert "conflict" in app.model_catalog.reason(RULE, "one/a")
    result = await app.registry.call("models.preview", {"models": RULE})
    assert [m["value"] for m in result["models"]] == ["two/b"]
