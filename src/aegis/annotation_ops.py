"""Render a stable crop of an annotated file for the agent.

Annotation messages carry addresses only (normalized region, PDF page, CSS
selector). ``annotation.view`` opens the file at a fixed size, independent of
the person's pane, and returns a PNG path the agent can read. It covers the
same kinds the person can mark: image, PDF and HTML.
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import time
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict, cast

from pydantic import BaseModel, Field

from . import attachments, files
from .fs_paths import resolve
from .ops import Caller, OpError

if TYPE_CHECKING:
    from .app import App

# Fixed render size so a crop does not depend on the person's pane.
VIEW_W, VIEW_H = 1280, 800
PDF_SCALE = 1.25


class Clip(TypedDict):
    x: float
    y: float
    width: float
    height: float


class Region(BaseModel):
    model_config = {"extra": "forbid"}
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    width: float = Field(ge=0, le=1)
    height: float = Field(ge=0, le=1)


class AnnotationView(BaseModel):
    model_config = {"extra": "forbid"}
    path: str = Field(description="A file under the config root the person annotated.")
    page: int | None = Field(
        None, ge=1, description="PDF page number; defaults to 1 for a PDF."
    )
    css: str | None = Field(
        None,
        description="CSS selector for an HTML element annotation (locator.css).",
    )
    region: Region | None = Field(
        None,
        description="Normalized 0–1 box on the image, PDF page, or HTML viewport.",
    )


def _clip(box: dict[str, float], region: Region) -> Clip:
    return {
        "x": float(box["x"] + region.x * box["width"]),
        "y": float(box["y"] + region.y * box["height"]),
        "width": float(max(1.0, region.width * box["width"])),
        "height": float(max(1.0, region.height * box["height"])),
    }


def register_annotation_ops(app: App) -> None:
    r = app.registry

    def out_dir(caller: Caller) -> Path:
        log_id = caller.log_id or ""
        if not log_id:
            raise OpError("no_session", "annotation.view needs a session")
        box = attachments.ensure_inbox(app.roots.state_root, log_id) / "annotation-views"
        box.mkdir(parents=True, exist_ok=True)
        return box

    def file_of(caller: Caller, rel: str) -> tuple[Path, str]:
        place = app.place_of(caller)
        path = resolve(place.root, place.guard, rel)
        if not path.is_file():
            raise OpError("not_found", f"{rel} is not a file")
        _mime, preview = files.classify(path)
        if preview not in ("image", "pdf", "html"):
            raise OpError(
                "unsupported",
                f"{rel} is {preview}; annotation.view takes image, PDF or HTML",
            )
        return path, preview

    async def render(path: Path, preview: str, p: AnnotationView, dest: Path) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise OpError(
                "browser_unavailable",
                "playwright is not installed; install it and run "
                "playwright install chromium",
            ) from e

        uri = path.resolve().as_uri()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            try:
                page = await browser.new_page(
                    viewport={"width": VIEW_W, "height": VIEW_H},
                    device_scale_factor=PDF_SCALE if preview == "pdf" else 1,
                )
                if preview == "image":
                    mime = mimetypes.guess_type(path.name)[0] or "image/png"
                    data = base64.b64encode(path.read_bytes()).decode("ascii")
                    await page.set_content(
                        f'<img id="a" src="data:{mime};base64,{data}" '
                        'style="display:block;max-width:none">'
                    )
                    img = page.locator("#a")
                    await img.wait_for(state="visible", timeout=15_000)
                    box = await img.bounding_box()
                    if not box or box["width"] < 1 or box["height"] < 1:
                        raise OpError("view_failed", "could not measure the image")
                    measured = cast(dict[str, float], box)
                    clip: Clip = (
                        _clip(measured, p.region)
                        if p.region
                        else {
                            "x": float(measured["x"]),
                            "y": float(measured["y"]),
                            "width": float(measured["width"]),
                            "height": float(measured["height"]),
                        }
                    )
                    await page.screenshot(path=str(dest), clip=clip)
                    return
                if preview == "html":
                    html = path.read_text(encoding="utf-8", errors="replace")
                    await page.set_content(html, wait_until="domcontentloaded")
                    if p.css:
                        loc = page.locator(p.css).first
                        try:
                            await loc.wait_for(state="visible", timeout=10_000)
                        except Exception as e:
                            raise OpError(
                                "not_found", f"nothing matches {p.css}"
                            ) from e
                        await loc.screenshot(path=str(dest), timeout=10_000)
                        return
                    if p.region:
                        await page.screenshot(
                            path=str(dest),
                            clip={
                                "x": p.region.x * VIEW_W,
                                "y": p.region.y * VIEW_H,
                                "width": max(1.0, p.region.width * VIEW_W),
                                "height": max(1.0, p.region.height * VIEW_H),
                            },
                        )
                        return
                    await page.screenshot(path=str(dest), full_page=False)
                    return
                page_n = p.page or 1
                await page.goto(
                    f"{uri}#page={page_n}",
                    wait_until="load",
                    timeout=30_000,
                )
                await asyncio.sleep(0.6)
                if p.region:
                    await page.screenshot(
                        path=str(dest),
                        clip={
                            "x": p.region.x * VIEW_W,
                            "y": p.region.y * VIEW_H,
                            "width": max(1.0, p.region.width * VIEW_W),
                            "height": max(1.0, p.region.height * VIEW_H),
                        },
                    )
                else:
                    await page.screenshot(path=str(dest), full_page=False)
            finally:
                await browser.close()

    @r.op(
        "annotation.view",
        AnnotationView,
        agent=True,
        read=True,
        access="read",
    )
    async def view(p: AnnotationView, caller: Caller):
        """Save a PNG of an annotated place at a fixed size, independent of
        the person's pane. Pass the file and its region, PDF page, or HTML
        css from the annotation JSON; read the returned path to see it."""
        path, preview = file_of(caller, p.path)
        if p.css and preview != "html":
            raise OpError("bad_params", "css applies only to HTML files")
        if p.page is not None and preview != "pdf":
            raise OpError("bad_params", "page applies only to PDF files")
        dest = out_dir(caller) / f"{time.strftime('%Y%m%d-%H%M%S')}-{path.stem}.png"
        try:
            await render(path, preview, p, dest)
        except OpError:
            raise
        except Exception as e:
            raise OpError("view_failed", str(e).splitlines()[0]) from None
        return {"path": str(dest), "kind": preview}
