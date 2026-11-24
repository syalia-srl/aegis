"""One real provider-rule launch; only zero-price OpenCode offers are eligible."""

import shutil

import pytest

from .conftest import until
from .test_agents import World

pytestmark = pytest.mark.live


async def test_real_free_provider_rule_answers_without_paid_fallback(
    tmp_path, fake_claude
):
    opencode = shutil.which("opencode")
    assert opencode, "OpenCode is required for the live free-provider check"
    (tmp_path / ".aegis.yaml").write_text(
        "agents:\n  free-check:\n    harness: opencode\n"
        "    models: {providers: [opencode], free: true}\n"
        "    effort: low\n    permission: read\n"
        "    primer: false\n    notices: false\n    isolated: true\n"
    )
    w = await World(tmp_path, fake_claude, opencode).start()
    try:
        await w.app.model_catalog.refresh()
        eligible = w.app.model_catalog.candidates(
            {"providers": ["opencode"], "free": True}
        )
        assert eligible, (
            w.app.model_catalog.error or "No eligible zero-price OpenCode offer"
        )
        r = await w.app.registry.call("session.spawn", {"agent": "free-check"})
        s = w.app.sessions.sessions[r["log_id"]]
        assert s.spec.model in eligible
        await s.send("Reply with exactly OK. Do not call tools.")
        await until(
            lambda: s.status == "idle", timeout=90, what="the real free model response"
        )
        prose = [e.get("md", "") for e in s.entries() if e["kind"] == "prose"]
        assert any("OK" in text for text in prose), (
            "The live free provider returned no OK response"
        )
        assert all(w.app.model_catalog.models[m].free for m in s.spec.cascade)
        print(f"Zero-price provider-rule response verified on {s.spec.model}")
    finally:
        await w.stop()
