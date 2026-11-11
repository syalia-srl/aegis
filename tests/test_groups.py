"""Named groups (#1039): a session belongs to a group record, filled by what it
spawns and enqueues, with no head. A session in no group that starts its first
child makes a group holding both, and the group outlives every member."""

import json
import re

from aegis.ops import Caller

from .conftest import until
from .test_agents import mcp, turn, world  # noqa: F401


async def child_of(world, parent):  # noqa: F811
    said = await turn(parent, mcp("session_spawn", agent="opus"))
    return world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])


async def test_a_first_spawn_makes_a_group_holding_parent_and_child(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    assert a.spec.group == b.spec.group
    assert re.match(r"^grp-[0-9a-f]{8}$", a.spec.group)
    assert world.app.sessions.groups.get(a.spec.group) is not None
    assert re.match(r"^[a-z]+-[a-z]+$", a.wire()["group_name"])


async def test_a_spawn_chain_shares_one_group(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    key = a.spec.group
    c = await child_of(world, b)
    assert a.spec.group == b.spec.group == c.spec.group == key


async def test_the_group_survives_the_first_session_closing_and_a_reload(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    key = a.spec.group
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    c = await child_of(world, b)
    assert c.spec.group == key
    assert world.app.sessions._session(c.meta()).spec.group == key


async def test_a_person_spawn_joins_the_tabs_group(world):  # noqa: F811
    a = await world.spawn()
    r = await world.app.registry.call(
        "session.send", {"log_id": a.log_id, "text": "/spawn opus"}, Caller("user")
    )
    b = world.session(r["log_id"])
    assert b.spec.group == a.spec.group
    assert b.spec.group.startswith("grp-")
    # A person started it: its questions are the person's, not a's (notices.py).
    assert b.spec.spawned_by is None

    r = await world.app.registry.call(
        "session.spawn", {"agent": "opus", "from_session": a.log_id}, Caller("user")
    )
    assert world.session(r["log_id"]).spec.group == a.spec.group


async def test_a_worker_joins_its_enqueuers_group(world):  # noqa: F811
    a = await world.spawn()
    said = await turn(a, mcp("queue_enqueue", queue="general", payload="/busy 1"))
    t = world.app.queues.tasks[json.loads(said.removeprefix("mcp ok: "))["task_id"]]
    await until(lambda: t.worker, timeout=8, what="the worker")
    assert world.session(t.worker).spec.group == a.spec.group
    assert a.spec.group.startswith("grp-")


async def test_a_handoff_keeps_the_group_and_leads_nothing(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    key = b.spec.group
    r = await world.app.registry.call(
        "session.handoff", {"log_id": b.log_id}, Caller("user")
    )
    new = world.session(r["log_id"])
    assert new.spec.group == key
    metas, _ = world.app.sessions.metas.read_all()
    assert [m["log_id"] for m in metas if m.get("leads")] == []
    assert "leads" not in new.wire()


async def test_the_last_member_archived_deletes_the_record(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    key = a.spec.group
    reg = world.app.sessions
    assert key in reg.groups.keys()
    await world.app.registry.call("session.close", {"log_id": a.log_id})
    assert key in reg.groups.keys()
    await world.app.registry.call("session.close", {"log_id": b.log_id})
    assert key not in reg.groups.keys()


async def test_reopening_a_member_whose_record_is_gone_stands_alone(world):  # noqa: F811
    a = await world.spawn()
    b = await child_of(world, a)
    for log_id in (a.log_id, b.log_id):
        await world.app.registry.call("session.close", {"log_id": log_id})
    wire = await world.app.registry.call("session.reopen", {"log_id": b.log_id})
    assert "group" not in wire
    assert world.session(b.log_id).spec.group is None


async def test_an_old_meta_with_leads_loads(world):  # noqa: F811
    a = await world.spawn()
    meta = a.meta()
    meta["leads"] = True
    s = world.app.sessions._session(meta)
    assert "leads" not in s.wire()


async def test_an_agent_cannot_move_its_child_into_another_group(world):  # noqa: F811
    a = await world.spawn()
    other = await world.spawn()
    said = await turn(a, mcp("session_spawn", agent="opus", from_session=other.log_id))
    child = world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])
    assert child.spec.group == a.spec.group


def test_a_far_spawn_names_the_group_once_across_servers():
    from aegis.app import far_group

    assert far_group("grp-3fa9c210", "alpha", "beta") == "grp-3fa9c210@alpha"
    # Already from a linked server: it keeps its server, not a second one.
    assert far_group("grp-3fa9c210@alpha", "beta", "gamma") == "grp-3fa9c210@alpha"
    # Back on the server it began on, it is a plain key again.
    assert far_group("grp-3fa9c210@alpha", "beta", "alpha") == "grp-3fa9c210"
