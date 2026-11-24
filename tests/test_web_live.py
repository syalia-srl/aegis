"""Verify native web sessions after their real Chrome round trips.

Start a fresh server from this checkout, spawn the four web presets and grant
their new tabs with Scripts enabled. Record provider -> log_id in
<root>/live-sessions.json. Run only this case with:
AEGIS_WEB_LIVE_ROOT=<root> uv run pytest --run-live tests/test_web_live.py
The setup uses the ordinary session.spawn operation, never a scratch bridge.
"""

import json
import os
from pathlib import Path

import pytest

from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store

pytestmark = pytest.mark.live


@pytest.mark.parametrize("provider", ["gemini", "deepseek", "qwen", "chatgpt"])
def test_native_web_provider_round_trip(provider):
    configured = os.environ.get("AEGIS_WEB_LIVE_ROOT")
    if not configured:
        pytest.skip(
            "Set AEGIS_WEB_LIVE_ROOT to a fresh server with granted Chrome tabs"
        )
    root = Path(configured)
    sessions = json.loads((root / "live-sessions.json").read_text())
    records, damaged = read_store(
        root / ".aegis/state/transcripts" / f"{sessions[provider]}.jsonl"
    )
    assert damaged == 0
    entries = fold_records(records).entries()
    proof = (root / f"web-live-{provider}.txt").read_text().strip()
    assert len(proof) == 16 and all(c in "0123456789abcdef" for c in proof)
    bash = [e for e in entries if e["kind"] == "tool" and e["title"] == "Bash"]
    assert len(bash) == 1 and bash[0]["status"] == "ok"
    raw = [json.loads(r["line"]) for r in records if r.get("src") == "web"]
    outputs = [r["text"] for r in raw if r["type"] == "tool_output"]
    assert len(outputs) == 1
    assert '"hits"' in outputs[0] and "Chrome" in outputs[0]
    assert "WEB_PROOF " + proof in outputs[0] and "exit_code: 0" in outputs[0]
    prose = [e["md"] for e in entries if e["kind"] == "prose"]
    assert any("WEB_NATIVE_OK" in text and proof in text for text in prose)
    assert any(r["type"] == "result" and not r["error"] for r in raw)
