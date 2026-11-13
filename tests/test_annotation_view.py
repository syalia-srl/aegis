"""annotation.view: a fixed-size crop of an annotated image, PDF or HTML."""

from pathlib import Path

import pytest

from aegis.app import App
from aegis.ops import Caller, OpError
from aegis.roots import make_roots

from .test_browser import PNG

CONFIG = (
    "default_agent: opus\n"
    "agents:\n"
    "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
)

HTML = """<!doctype html><title>t</title>
<body><h1 id="hero">Hello</h1><p class="note">body</p></body>
"""


@pytest.fixture
async def app(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    a = App(make_roots(tmp_path, None), claude_bin=fake_claude, interrupt_timeout=0.5)
    await a.boot()
    yield a
    await a.shutdown()


async def spawn(app) -> str:
    return (await app.registry.call("session.spawn", {"agent": "opus"}))["log_id"]


@pytest.mark.slow
async def test_annotation_view_crops_an_image_at_a_fixed_size(app, tmp_path):
    (tmp_path / "chart.png").write_bytes(PNG)
    lid = await spawn(app)
    agent = Caller("agent", log_id=lid)
    out = await app.registry.call(
        "annotation.view",
        {
            "path": "chart.png",
            "region": {"x": 0, "y": 0, "width": 1, "height": 1},
        },
        agent,
    )
    path = Path(out["path"])
    assert out["kind"] == "image"
    assert path.is_file() and path.suffix == ".png"
    assert path.stat().st_size > 20
    assert path.parent.name == "annotation-views"


@pytest.mark.slow
async def test_annotation_view_shots_an_html_element(app, tmp_path):
    (tmp_path / "page.html").write_text(HTML)
    lid = await spawn(app)
    agent = Caller("agent", log_id=lid)
    out = await app.registry.call(
        "annotation.view",
        {"path": "page.html", "css": "#hero"},
        agent,
    )
    assert out["kind"] == "html"
    assert Path(out["path"]).is_file()


async def test_annotation_view_refuses_css_on_an_image(app, tmp_path):
    (tmp_path / "chart.png").write_bytes(PNG)
    lid = await spawn(app)
    with pytest.raises(OpError, match="css applies only"):
        await app.registry.call(
            "annotation.view",
            {"path": "chart.png", "css": "#x"},
            Caller("agent", log_id=lid),
        )


async def test_annotation_view_refuses_unsupported_kinds(app, tmp_path):
    (tmp_path / "note.txt").write_text("hi")
    lid = await spawn(app)
    with pytest.raises(OpError, match="unsupported"):
        await app.registry.call(
            "annotation.view",
            {"path": "note.txt"},
            Caller("agent", log_id=lid),
        )
