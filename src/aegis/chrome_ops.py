"""The operations behind Settings → Chrome (chrome.py): a pairing code,
unpairing a browser, and the theme the extension draws in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from .chrome import Chrome

if TYPE_CHECKING:
    from .app import App


class ChromeDevice(BaseModel):
    id: str


class ChromeTheme(BaseModel):
    vars: dict[str, str]


def register_chrome_ops(app: App) -> Chrome:
    r = app.registry
    chrome = Chrome(app.roots.state_root, app.publish)

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

    return chrome
