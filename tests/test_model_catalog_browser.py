"""The real Models page creates and edits fixed and provider-rule agents."""

# ruff: noqa: F811
import json
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from .test_browser import CONFIG, RANKING, Server, browser, page, pick  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def catalog_server(tmp_path: Path, fake_claude, fake_opencode):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    data = {
        **RANKING,
        "catalog_refreshed": "2026-11-24T12:00:00+00:00",
        "offers": {
            "unconnected/sample": {
                "provider": "unconnected",
                "provider_name": "Unconnected",
                "label": "Public sample",
                "free": True,
                "price_in": 0,
                "price_out": 0,
            },
        },
    }
    (tmp_path / "models.json").write_text(json.dumps(data))
    s = Server(tmp_path, fake_claude, fake_opencode)
    s.args = ["--name", "preview"]
    s.env["AEGIS_MODELS_FILE"] = str(tmp_path / "models.json")
    s.start()
    yield s
    s.stop()


@pytest.mark.parametrize("width", [1280, 390])
def test_catalog_creates_fixed_and_dynamic_agents(catalog_server, page, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(catalog_server.url + "#models")
    page.wait_for_selector('#models .mdl-row[data-model="opencode-go/fake-flash"]')
    assert page.locator(
        '#models .mdl-row[data-model="unconnected/sample"] input'
    ).is_disabled()
    assert page.locator('[aria-label="Agent name"]').is_visible()
    assert page.evaluate("document.documentElement.scrollWidth") == width
    page.check('[aria-label="Select opencode-go/fake-flash"]')
    pick(page, 'pick-chip[aria-label="Price filter"]', "Any price")
    page.check('[aria-label="Select opencode-go/fake-pro"]')
    page.get_by_role("button", name="Compare selected", exact=True).click()
    page.wait_for_selector("dialog[open] .mdl-compare-card")
    assert page.locator("dialog[open] .mdl-compare-card").count() == 2
    page.locator("dialog[open]").get_by_role("button", name="Close", exact=True).click()
    page.fill('[aria-label="Agent name"]', "fixed-helper")
    page.get_by_role("button", name="Review agent", exact=True).click()
    page.locator("dialog[open]").get_by_role(
        "button", name="Save agent", exact=True
    ).click()
    page.wait_for_function(
        "document.querySelector('.mdl-feedback').textContent.includes('fixed-helper saved')"
    )
    doc = YAML(typ="safe").load((catalog_server.root / ".aegis.yaml").read_text())
    assert doc["agents"]["fixed-helper"]["models"] == [
        "opencode-go/fake-flash",
        "opencode-go/fake-pro",
    ]
    assert doc["default_agent"] == "opus"
    page.fill('[aria-label="Agent name"]', "free-rule")
    pick(page, 'pick-chip[aria-label="Model selection mode"]', "Follow free providers")
    assert page.get_by_role("button", name="Review agent", exact=True).is_disabled()
    pick(page, 'pick-chip[aria-label="Price filter"]', "Free only")
    page.check('[aria-label="Provider opencode-go"]')
    page.get_by_role("button", name="Review agent", exact=True).click()
    page.locator("dialog[open]").get_by_role(
        "button", name="Save agent", exact=True
    ).click()
    page.wait_for_function(
        "document.querySelector('.mdl-feedback').textContent.includes('free-rule saved')"
    )
    doc = YAML(typ="safe").load((catalog_server.root / ".aegis.yaml").read_text())
    assert doc["agents"]["free-rule"]["models"] == {
        "providers": ["opencode-go"],
        "free": True,
    }
    pick(page, 'pick-chip[aria-label="Edit agent"]', "free-rule")
    page.wait_for_function(
        "document.querySelector('[aria-label=\"Model selection mode\"]').value === 'dynamic'"
    )
    assert page.locator('[aria-label="Agent name"]').is_disabled()
    page.wait_for_function(
        "[...document.querySelectorAll('.mdl-providers input:checked')].some(o => o.value === 'opencode-go')"
    )
    page.wait_for_selector('[aria-label="Provider unconnected"]')
    page.locator(".v-models").evaluate("node => node.scrollTop = 0")
    page.screenshot(
        path=str(catalog_server.root / f"models-{width}.png"), full_page=True
    )
    page.locator(".mdl-builder").screenshot(
        path=str(catalog_server.root / f"models-builder-{width}.png")
    )
    assert page.locator(".v-models").evaluate("node => node.scrollWidth") <= width
    page.goto(catalog_server.url + "#settings")
    page.wait_for_selector('#set-agents a[href="#models"]')
    page.fill(
        '[data-row="agents.free-rule"] textarea.set-description',
        "Reviewed free-provider helper",
    )
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    doc = YAML(typ="safe").load((catalog_server.root / ".aegis.yaml").read_text())
    assert doc["agents"]["free-rule"]["models"] == {
        "providers": ["opencode-go"],
        "free": True,
    }
    page.goto(catalog_server.url + "#new")
    page.wait_for_selector("#sp-agent")
    pick(page, "#sp-agent", "free-rule")
    page.fill("#sp-text", "Say hello")
    page.click("#sp-go")
    page.wait_for_selector("#a2[data-view=session]")
    assert page.errors == []
