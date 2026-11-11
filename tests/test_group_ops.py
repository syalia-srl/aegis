"""Changing a session's group and a group's name (#1039): the person's ops,
``group.set`` and ``group.rename``, and the agent tool ``session_group``."""

import json
import re

import pytest

from aegis.ops import OpError

from .conftest import until
from .test_agents import mcp, turn, world  # noqa: F401


async def child_of(world, parent):  # noqa: F811
    said = await turn(parent, mcp("session_spawn", agent="opus"))
    return world.session(json.loads(said.removeprefix("mcp ok: "))["log_id"])


async def test_set_new_moves_sessions_into_one_fresh_group(world):  # noqa: F811
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id], "new", world.app.user)
    assert re.match(r"^grp-[0-9a-f]{8}$", key)
    assert a.spec.group == b.spec.group == key
    g = reg.groups.get(key)
    assert g is not None and re.match(r"^[a-z]+-[a-z]+$", g.name)
    assert a.wire()["group_name"] == b.wire()["group_name"] == g.name
    assert json.loads(reg.metas.path(a.log_id).read_text())["group"] == key


async def test_set_null_leaves_and_an_emptied_group_is_deleted(world):  # noqa: F811
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id], "new", world.app.user)
    assert reg.set_group([a.log_id], None, world.app.user) is None
    assert a.spec.group is None and "group_name" not in a.wire()
    assert key in reg.groups.keys()  # b still holds it
    reg.set_group([b.log_id], None, world.app.user)
    assert key not in reg.groups.keys()


async def test_a_session_that_left_and_spawns_makes_a_new_group(world):  # noqa: F811
    reg = world.app.sessions
    a = await world.spawn()
    b = await child_of(world, a)
    old = a.spec.group
    assert old == b.spec.group
    reg.set_group([a.log_id], None, world.app.user)
    c = await child_of(world, a)
    assert a.spec.group == c.spec.group
    assert a.spec.group not in (None, old)
    # What it spawned before stayed behind.
    assert b.spec.group == old and old in reg.groups.keys()


async def test_rename_reaches_every_members_wire(world):  # noqa: F811
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id], "new", world.app.user)
    assert reg.rename_group(key, "the-bots")["name"] == "the-bots"
    assert a.wire()["group_name"] == b.wire()["group_name"] == "the-bots"
    assert a.wire()["group"] == key


async def test_a_far_session_is_refused(world):  # noqa: F811
    reg = world.app.sessions
    a = await world.spawn()
    with pytest.raises(OpError) as e:
        reg.set_group([f"{a.log_id}@beta"], "new", world.app.user)
    assert e.value.code == "far_session"
    assert a.spec.group is None


async def test_another_owners_session_is_refused(world):  # noqa: F811
    reg = world.app.sessions
    a = await world.spawn()
    with pytest.raises(OpError) as e:
        reg.set_group([a.log_id], "new", "someone-else")
    assert e.value.code == "not_yours"
    assert a.spec.group is None


async def test_session_group_creates_joins_leaves_and_renames(world):  # noqa: F811
    reg = world.app.sessions
    a = await world.spawn()
    b = await child_of(world, a)
    # A new group with the name the agent chose holds both.
    await turn(a, mcp("session_group", handles=[a.handle, b.handle], name="mine"))
    key = a.spec.group
    assert b.spec.group == key and reg.groups.get(key).name == "mine"
    # Another open session of the same owner joins it by name.
    d = await world.spawn()
    await turn(a, mcp("session_group", handles=[d.handle], name="mine"))
    assert d.spec.group == key
    # Leaving takes only that session out.
    await turn(a, mcp("session_group", handles=[d.handle]))
    assert d.spec.group is None and a.spec.group == key
    # Renaming names it for every member.
    await turn(a, mcp("session_group", group="mine", name="ours"))
    assert a.wire()["group_name"] == b.wire()["group_name"] == "ours"
    await until(lambda: a.status == "idle", timeout=8, what="the last turn")
