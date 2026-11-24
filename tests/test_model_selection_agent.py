"""A real fake-harness process launches from a provider rule and rejects paid overrides."""

import pytest

from aegis.ops import OpError
from .test_agents import CONFIG, World


async def test_dynamic_agent_launches_and_rejects_paid_changes(
    tmp_path, fake_claude, fake_opencode
):
    cfg = CONFIG
    cfg = cfg.replace(
        "queues:\n",
        "  free-rule:\n    harness: opencode\n    models: {providers: [opencode-go], free: true}\n    effort: high\n    permission: read\nqueues:\n",
    )
    (tmp_path / ".aegis.yaml").write_text(cfg)
    w = await World(tmp_path, fake_claude, fake_opencode).start()
    try:
        await w.app.model_catalog.refresh()
        r = await w.app.registry.call("session.spawn", {"agent": "free-rule"})
        s = w.app.sessions.sessions[r["log_id"]]
        assert s.spec.model_selector == {"providers": ["opencode-go"], "free": True}
        assert s.spec.model in s.spec.cascade
        assert all(w.app.model_catalog.models[m].free for m in s.spec.cascade)
        with pytest.raises(OpError, match="zero input/output"):
            await s.configure(model="opencode-go/fake-pro")
        assert s.spec.model != "opencode-go/fake-pro"
        assert s.meta()["model_selector"] == s.spec.model_selector
    finally:
        await w.stop()
