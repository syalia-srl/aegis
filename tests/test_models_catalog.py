"""A harness process queries the public catalogue through the real MCP endpoint."""

import json

import pytest

from aegis import ranking

from .test_agents import mcp, turn, world  # noqa: F401


@pytest.fixture(autouse=True)
def clear_ranking():
    ranking.ranking.cache_clear()
    yield
    ranking.ranking.cache_clear()


async def test_agent_queries_catalog_without_provider_login(world, monkeypatch):  # noqa: F811
    path = world.root / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "refreshed": "2026-11-24",
                "catalog_refreshed": "2026-11-24T12:00:00+00:00",
                "models": {"public/model": {"aa": "model", "intelligence": 50.0}},
                "offers": {
                    "public/model": {
                        "provider": "public",
                        "free": True,
                        "aa": "model",
                        "price_in": 0,
                        "price_out": 0,
                    }
                },
            }
        )
    )
    monkeypatch.setenv("AEGIS_MODELS_FILE", str(path))
    s = await world.spawn()
    await s.configure(permission="read")
    said = await turn(s, mcp("models_catalog", provider="public", free=True))
    assert said.startswith("mcp ok: "), said
    result = json.loads(said.removeprefix("mcp ok: "))
    assert result["total"] == 1
    assert result["models"][0]["rank"] == 1
    assert result["models"][0]["free"] is True
    print("Read-only agent → real MCP models_catalog:")
    print(json.dumps(result, indent=2))
