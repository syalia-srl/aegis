"""Settings' MCP servers section (#960): add a server, test it, attach it to an
agent, save. Kept out of test_browser.py, whose server and page it borrows."""

import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

from .test_browser import (  # noqa: F401
    WIDER,
    browser,
    open_settings,
    pick,
    settings_server,
)

pytestmark = [pytest.mark.browser, pytest.mark.slow]

ECHO = Path(__file__).parent / "fixtures" / "mcp_echo.py"
ROW = '.set-queue[data-row="mcp.echo"]'


@pytest.mark.parametrize("width", [1280, 390])
def test_settings_adds_an_mcp_server(settings_server, browser, width):  # noqa: F811
    errors: list = []
    page = browser.new_context(viewport={"width": width, "height": 900}).new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    open_settings(page, settings_server.url)
    page.click("#set-add-mcp")
    new = '.set-mcp-row[data-row="mcp."]'
    page.fill(f"{new} input[name=name]", "echo")
    pick(page, f"{ROW} pick-chip[name=kind]", "command")
    page.fill(f"{ROW} input[name=command]", sys.executable)
    page.click(f"{ROW} .set-priming summary")
    page.fill(f"{ROW} textarea[name=args]", str(ECHO))
    page.click(f"{ROW} button:has-text('Test')")
    page.wait_for_function(
        "s => document.querySelector(s)?.textContent.startsWith('1 tool')",
        arg=f"{ROW} .set-mcp-test",
        timeout=15000,
    )
    assert "ping" in page.text_content(f"{ROW} .set-mcp-test")
    assert page.evaluate(WIDER.replace("#a2 *", "#set-mcp *")) == []
    agent = '.set-agent[data-row="agents.opus"]'
    page.click(f"{agent} .set-mcp summary")
    page.click(f"{agent} .set-mcp button:has-text('echo')")
    page.click("#set-save")
    page.wait_for_function(
        "document.querySelector('#set-status').textContent === 'Saved'"
    )
    data = YAML(typ="safe").load((settings_server.root / ".aegis.yaml").read_text())
    assert data["mcp"] == {"echo": {"command": sys.executable, "args": [str(ECHO)]}}
    assert data["agents"]["opus"]["mcp"] == ["echo"]
    assert errors == []
