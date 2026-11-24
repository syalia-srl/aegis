# ruff: noqa: F811
"""The real MCP endpoint can propose cleanup, never approve its own proposal."""

import json

import pytest

from aegis.ops import Caller, OpError
from aegis.transcript.entries import fold_records
from aegis.transcript.store import read_store

from .test_agents import mcp, turn, world  # noqa: F401
from .test_repo_gc import repository


async def proposal(s):
    said = await turn(s, mcp("gc_propose"), timeout=15)
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


@pytest.mark.slow
async def test_mcp_only_proposes_and_reads_and_user_selection_executes(world):
    repo = repository(world.root)
    s = await world.spawn()
    p = await proposal(s)
    assert (repo / "target").exists()
    assert s.fold().entry(p["id"])["status"] == "pending"
    result = await turn(s, mcp("gc_read", id=p["id"]))
    assert '"status": "pending"' in result
    with pytest.raises(OpError) as err:
        await world.app.registry.call(
            "gc.decide",
            {"id": p["id"], "approve": True, "paths": [str(repo / "target")]},
            Caller("agent", s.log_id),
        )
    assert err.value.code == "not_for_agents"
    # The real MCP projection must not advertise an approval tool either.
    assert "gc_decide" not in {op.tool_name for op in world.app.registry.agent_ops()}
    with pytest.raises(OpError, match="select eligible"):
        await world.app.registry.call(
            "gc.decide",
            {
                "log_id": s.log_id,
                "id": p["id"],
                "approve": True,
                "paths": [str(world.root)],
            },
        )
    result = await world.app.registry.call(
        "gc.decide",
        {
            "log_id": s.log_id,
            "id": p["id"],
            "approve": True,
            "paths": [str(repo / "target")],
        },
    )
    assert result["result"]["removed"] == [str(repo / "target")]
    assert (repo / ".venv").exists() and not (repo / "target").exists()
    assert (
        fold_records(read_store(s.store.path)[0]).entry(p["id"])["status"] == "finished"
    )
    with pytest.raises(OpError, match="already been answered"):
        await world.app.registry.call(
            "gc.decide",
            {
                "log_id": s.log_id,
                "id": p["id"],
                "approve": True,
                "paths": [str(repo / "target")],
            },
        )


async def test_slash_command_and_cancellation_survive_restart(world):
    repo = repository(world.root)
    s = await world.spawn()
    got = await world.app.registry.call(
        "session.send", {"log_id": s.log_id, "text": "/gc 30"}
    )
    id = got["id"]
    assert not [e for e in s.entries() if e["kind"] == "user"]
    log_id = s.log_id
    await world.restart()
    s = world.session(log_id)
    assert s.fold().entry(id)["status"] == "pending"
    result = await world.app.registry.call(
        "gc.decide", {"log_id": log_id, "id": id, "approve": False}
    )
    assert result["status"] == "cancelled"
    assert (repo / "target").exists() and (repo / ".venv").exists()
