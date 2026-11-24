"""Codex quota from a fresh server, through its RPC probe to the real client."""

import json
import os
import time
from pathlib import Path

import pytest

from .test_browser import Server, browser, new_page, spawn  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


@pytest.fixture
def codex_quota_server(tmp_path, fake_claude, fake_codex, monkeypatch):
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: cx\nagents:\n"
        "  cx: {harness: codex, model: openai/fake-pro, effort: high, permission: full}\n"
        "notices: {disk: false, memory: false, cpu: false, quota: false}\n"
    )
    Path(os.environ["CODEX_AUTH"]).write_text(
        json.dumps({"auth_mode": "chatgpt", "tokens": {"account_id": "test-account"}})
    )
    monkeypatch.setenv(
        "PATH", str(Path(fake_codex).parent) + os.pathsep + os.environ["PATH"]
    )
    monkeypatch.setenv(
        "FAKE_CODEX_LIMITS",
        json.dumps(
            {
                "rateLimits": {
                    "primary": {
                        "usedPercent": 12,
                        "windowDurationMins": 10080,
                        "resetsAt": int(time.time()) + 2 * 86400,
                    }
                }
            }
        ),
    )
    server = Server(tmp_path, fake_claude)
    server.args = ["--codex", fake_codex]
    server.start()
    yield server
    server.stop()


@pytest.mark.parametrize("width", [1280, 390])
def test_codex_weekly_quota_is_visible_at_both_widths(
    codex_quota_server,
    browser,  # noqa: F811
    width,
):
    errors = []
    page = new_page(browser, errors)
    page.set_viewport_size({"width": width, "height": 900})
    try:
        page.goto(codex_quota_server.url)
        week = page.locator("#band .gauge[data-kind=weekly_all]")
        week.wait_for()
        assert "12%" in week.inner_text()
        assert "ChatGPT" in page.inner_text("#band")
        spawn(page, "hello")
        tile = page.locator("#s-tiles .tile[data-kind=weekly_all]")
        tile.wait_for()
        assert "12%" in tile.inner_text()
        assert page.evaluate("document.documentElement.scrollWidth") <= width
        if width == 390:
            page.click("#nav-side")
            page.locator("#s-tiles").wait_for()
        if folder := os.environ.get("AEGIS_PR_SHOTS"):
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            page.screenshot(
                path=str(target / f"codex-quota-{width}.png"), full_page=True
            )
        assert errors == []
    finally:
        page.close()
