"""A schedule's runs keep the tags of the session that made it (#959)."""

from __future__ import annotations

import dataclasses

from .test_agents import mcp, turn
from .test_schedule_ops import NEW, ok, world  # noqa: F401


async def test_a_schedules_runs_keep_its_makers_tags(world):  # noqa: F811
    a = await world.spawn()
    a.spec = dataclasses.replace(a.spec, tags={"org": "north"})
    ok(await turn(a, mcp("schedule_create", name="pr-triage", **NEW)))
    s = world.app.schedules.items["pr-triage"]
    assert s.owner["tags"] == {"org": "north"}
    assert world.app.schedules._spec(s).tags == {"org": "north"}
