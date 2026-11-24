"""A Claude session's text streams into the browser as it is generated (#79).

Kept out of test_browser.py, whose fixtures it borrows, so the two can change
apart."""

import os
from pathlib import Path

import pytest

from .test_browser import browser, page, server, spawn, turns_done  # noqa: F401

pytestmark = [pytest.mark.browser, pytest.mark.slow]


def test_a_claude_session_draws_text_before_the_turn_ends(server, page):  # noqa: F811
    page.goto(server.url)
    spawn(page)
    # Subscribed before the stream starts: the text can only arrive as patches.
    page.fill("#input", "/stream 6")
    page.press("#input", "Enter")
    page.wait_for_function(
        "[...document.querySelectorAll('.row.prose .body')]"
        ".some(b => b.textContent.includes('chunk1'))"
    )
    assert "chunk6" not in page.inner_text("#entries"), "drawn while it streams"
    turns_done(page, 1)
    # The whole block replaced the streamed row: one row, the whole text.
    bodies = page.locator(".row.prose .body").all_inner_texts()
    assert [b.strip() for b in bodies] == ["chunk1 chunk2 chunk3 chunk4 chunk5 chunk6"]
    assert page.errors == []


@pytest.mark.parametrize("width", [1280, 390])
def test_streamed_letters_fade_and_history_is_immediate(server, page, width):  # noqa: F811
    page.set_viewport_size({"width": width, "height": 800})
    page.goto(server.url)
    sid = spawn(page)
    page.evaluate("""() => {
      window.streamFrames = [];
      window.fadedText = [];
      window.recordStream = true;
      const tick = () => {
        const chars = [...document.querySelectorAll('.row.prose .stream-char')];
        if (chars.length) {
          window.streamFrames.push(chars.map(c => +getComputedStyle(c).opacity));
          window.fadedText.push(chars.map(c => c.textContent).join(''));
        }
        if (window.recordStream) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    }""")
    page.fill("#input", "/stream 6")
    page.press("#input", "Enter")
    page.wait_for_function(
        "window.streamFrames.some(f => f.some(x => x > 0 && x < 1))", timeout=5000
    )
    shots = os.environ.get("AEGIS_STREAM_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"stream-{width}.png"))
    turns_done(page, 1)
    frames = page.evaluate(
        "() => { window.recordStream = false; return window.streamFrames; }"
    )
    assert any(max(f) - min(f) > 0.1 for f in frames), "letters fade at different times"
    assert page.evaluate("window.fadedText.some(t => t.includes('chunk2'))"), (
        "later tokens fade too"
    )
    page.wait_for_function(
        "[...document.querySelectorAll('.stream-char')].every(c => +getComputedStyle(c).opacity === 1)"
    )
    body = page.locator(".row.prose .body")
    assert body.inner_text().strip() == "chunk1 chunk2 chunk3 chunk4 chunk5 chunk6"
    if shots:
        page.screenshot(path=str(Path(shots) / f"complete-{width}.png"))
    page.goto(f"{server.url}#s={sid}")
    page.wait_for_selector(".row.prose .body")
    assert page.locator(".stream-char").count() == 0, "history never replays"
    assert page.errors == []


def transcript_for_patches(pg, srv):
    pg.goto(srv.url)
    pg.evaluate("""async () => {
      const { Transcript } = await import('/static/js/transcript.js');
      const sc = document.createElement('div');
      sc.className = 'aegis';
      sc.style.cssText = 'position:fixed;inset:0;overflow:auto;z-index:9999';
      const list = document.createElement('div');
      sc.append(list);
      document.body.append(sc);
      window.streamTranscript = new Transcript(sc, list, document.createElement('button'));
      window.patchReply = (md, live = true, id = 'fade-test') => ({
        upsert: {id, kind: 'prose', rev: 1, md, live, unread: true}
      });
      window.streamTranscript.snapshot({entries: [], rev: 0});
    }""")


def test_overlapping_patches_preserve_progress_and_finish_within_300ms(server, page):  # noqa: F811
    transcript_for_patches(page, server)
    result = page.evaluate("""async () => {
      const t = window.streamTranscript;
      t.apply([patchReply('First letters')]);
      const first = document.querySelector('.stream-char').getAnimations()[0];
      await first.ready;
      await new Promise(resolve => setTimeout(resolve, 60));
      const before = +getComputedStyle(document.querySelector('.stream-char')).opacity;
      t.apply([patchReply('First letters and more 👩🏽‍💻 é')]);
      const spans = [...document.querySelectorAll('.stream-char')];
      const after = +getComputedStyle(spans[0]).opacity;
      const graphemes = spans.map(s => s.textContent);
      // A large burst must bound both the animated tail and its timeline.
      const md = 'First letters and more 👩🏽‍💻 é ' + 'abcdef'.repeat(1000);
      t.apply([patchReply(md)]);
      const chars = [...document.querySelectorAll('.stream-char')];
      const end = Math.max(...chars.map(c => {
        const timing = c.getAnimations()[0].effect.getTiming();
        return timing.delay + timing.duration;
      }));
      // Finalization under another id, as Claude's whole block arrives.
      t.apply([{remove: 'fade-test'}, patchReply(md, false, 'final-test')]);
      return {before, after, graphemes, count: chars.length, end,
        text: document.querySelector('.row.prose .body').textContent};
    }""")
    assert result["before"] > 0
    assert result["after"] >= result["before"] - 0.15, (
        "a patch must not restart old letters"
    )
    assert "👩🏽‍💻" in result["graphemes"] and "é" in result["graphemes"]
    assert 0 < result["count"] <= 120
    assert result["end"] <= 300
    assert result["text"].rstrip().endswith("abcdef" * 1000)
    page.wait_for_function(
        "[...document.querySelectorAll('.stream-char')].every(c => +getComputedStyle(c).opacity === 1)"
    )
    assert page.errors == []


def test_reduced_motion_and_markdown_reconciliation_do_not_replay(server, page):  # noqa: F811
    transcript_for_patches(page, server)
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("streamTranscript.apply([patchReply('No animation')])")
    assert page.locator(".stream-char").count() == 0
    page.emulate_media(reduced_motion="no-preference")
    page.evaluate("""() => {
      streamTranscript.apply([patchReply('**formatted')]);
      streamTranscript.apply([patchReply('**formatted** text')]);
    }""")
    assert page.locator(".row.prose strong").inner_text() == "formatted"
    assert page.locator(".stream-char").count() == 0
    page.evaluate(
        "streamTranscript.resume({removed: [], entries: [patchReply('Resumed history').upsert], rev: 2})"
    )
    assert page.locator(".stream-char").count() == 0
    assert page.errors == []
