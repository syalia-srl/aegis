"""Real harness gc tools against the local fake model; spends no model quota."""

import pytest

from ..conftest import until
from ..test_repo_gc import repository
from .fakemodel import Call, Rule, Say

pytestmark = pytest.mark.live


async def test_real_harness_repository_gc_proposes_then_waits_for_the_person(canary):
    repo = repository(canary.root)
    canary.model.script(
        Rule("CANARY-GC", [Call("gc_propose"), Say("GC-WAITING")]),
        Rule("from gc:", [Say("GC-CANCELLED")]),
    )
    s = await canary.spawn()
    await canary.send(s, "CANARY-GC")
    await until(
        lambda: s.status == "idle" and any(e["kind"] == "gc" for e in s.entries()),
        timeout=120,
        what="the real harness's gc proposal and turn end",
    )
    card = next(e for e in s.entries() if e["kind"] == "gc")
    assert card["status"] == "pending" and (repo / "target").exists()
    assert any(not r["reason"] for r in card["detail"]["inventory"]["rows"])
    await canary.app.registry.call(
        "gc.decide", {"log_id": s.log_id, "id": card["id"], "approve": False}
    )
    await until(
        lambda: (
            s.status == "idle"
            and any("GC-CANCELLED" in (e.get("md") or "") for e in s.entries())
        ),
        timeout=120,
        what="the real harness's cancellation wake",
    )
    assert (repo / "target").exists()
