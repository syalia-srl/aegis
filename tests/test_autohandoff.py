"""A handoff carries what the session waits on, and /autohandoff hands off on
its own past a context size (#1019)."""

import pytest

from aegis.ops import Caller, OpError
from aegis.queues import Task
from aegis.schedule_log import Definition

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_context_e2e import end, first_message, handoff


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def send(world, s, text):
    params = {"log_id": s.log_id, "text": text}
    return await world.app.registry.call("session.send", params, Caller("user"))


async def monitor(world, a) -> str:
    params = {"description": "a long build", "done": "false", "progress": None}
    r = await world.app.registry.call(
        "monitor.start", params | {"source": None}, Caller("agent", a.log_id)
    )
    return r["monitor_id"]


def task(world, a) -> Task:
    t = Task("t-1", "small", "do it", True, a.log_id, str(world.root))
    world.app.queues.tasks[t.id] = t
    return t


def schedule(world, a) -> None:
    raw = {
        "clock": {"every": "5m"},
        "fire": {"target": "existing", "session": a.log_id},
        "prompt": "keep going",
    }
    world.app.schedules.create(
        "nudge-me",
        Definition.model_validate(raw),
        {"kind": "user", "principal": world.app.user},
        world.root,
    )


def successor(world, a):
    return next(
        (
            s
            for s in world.app.sessions.sessions.values()
            if s.spec.continues == a.log_id
        ),
        None,
    )


async def test_handoff_carries_monitors_tasks_and_schedules(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    mon = await monitor(world, a)
    t = task(world, a)
    schedule(world, a)
    r = await send(world, a, "/handoff go on")
    b = world.session(r["log_id"])
    assert world.app.monitors.items[mon].owner == b.log_id
    assert t.enqueuer == b.log_id, (t.status, t.error)
    assert world.app.schedules.items["nudge-me"].d.fire.session == b.log_id
    await until(lambda: any(e["kind"] == "user" for e in b.entries()), 8, "its prompt")
    said = first_message(b)
    assert "1 monitor" in said and "1 queue task" in said and "1 schedule" in said
    reg = world.app.sessions
    await until(lambda: a.log_id in reg.archived, 8, "the old session archived")
    assert mon in world.app.monitors.items  # archiving the old one dropped nothing


async def test_the_chip_carries_them_too(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    mon = await monitor(world, a)
    r = await handoff(world, a)
    assert world.app.monitors.items[mon].owner == r["log_id"]


async def test_a_successor_closed_before_answering_gives_them_back(world):
    a = await world.spawn()
    await turn(a, "/ctx 342000")
    await end(world, a, handoff="[[slow]] Brief.")
    mon = await monitor(world, a)
    t = task(world, a)
    r = await handoff(world, a)
    await world.app.sessions.close(r["log_id"])
    assert world.app.monitors.items[mon].owner == a.log_id
    assert t.enqueuer == a.log_id and a.continued_by is None


async def test_a_background_task_still_refuses(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    a.open_tasks = {"bg-1"}
    with pytest.raises(OpError) as e:
        await send(world, a, "/handoff go on")
    assert e.value.code == "has_wakers" and "background task" in e.value.message


async def test_autohandoff_takes_n_off_and_the_default(world):
    a = await world.spawn()
    assert (await send(world, a, "/autohandoff 200000"))["autohandoff"] == 200000
    assert (await send(world, a, "/autohandoff off"))["autohandoff"] is None
    assert (await send(world, a, "/autohandoff"))["autohandoff"] == 300000
    with pytest.raises(OpError) as e:
        await send(world, a, "/autohandoff soon")
    assert e.value.code == "bad_params"


async def test_below_n_nothing_happens(world):
    a = await world.spawn()
    await send(world, a, "/autohandoff 200000")
    await turn(a, "/ctx 150000")
    said = await end(world, a, attention="review")
    assert "autohandoff" not in said.lower() and a.continued_by is None


async def test_past_n_it_asks_for_the_brief(world):
    a = await world.spawn()
    await send(world, a, "/autohandoff 200000")
    await turn(a, "/ctx 250000")
    asked = await end(world, a, attention="needs_you", line="Merge or rebase?")
    assert "autohandoff from 200k" in asked and a.continued_by is None


async def test_a_brief_past_n_hands_off_when_the_turn_ends(world):
    a = await world.spawn()
    world.app.sessions.rename(a.log_id, a.handle, "A named session")
    # The fake's turn reports about 1k of context when it calls turn_end.
    await send(world, a, "/autohandoff 500")
    mon = await monitor(world, a)
    brief = mcp("turn_end", attention="review", line="x", handoff="Done: X. Next: Y.")
    await turn(a, brief)
    await until(lambda: successor(world, a) is not None, 8, "the new session")
    b = successor(world, a)
    await until(lambda: any(e["kind"] == "user" for e in b.entries()), 8, "its prompt")
    assert "Done: X. Next: Y." in first_message(b)
    assert "1 monitor" in first_message(b)
    assert world.app.monitors.items[mon].owner == b.log_id


async def test_a_background_task_holds_the_autohandoff(world):
    a = await world.spawn()
    await send(world, a, "/autohandoff 200000")
    await turn(a, "/ctx 250000")
    a.open_tasks = {"bg-1"}
    said = await end(world, a, attention="review", handoff="Brief.")
    assert said.startswith("noted. Autohandoff waits: 1 background task")
    assert a.continued_by is None


def lines(s) -> list[str]:
    return [e.get("summary") or "" for e in s.entries() if e["kind"] == "system"]


async def test_both_transcripts_say_what_the_handoff_carried(world):
    # #1022: the person sees the handoff in each tab, not only in the prompt.
    a = await world.spawn()
    await turn(a, "earlier work")
    await monitor(world, a)
    r = await send(world, a, "/handoff go on")
    b = world.session(r["log_id"])
    assert f"continued in {b.handle}, carrying 1 monitor" in lines(a)
    assert f"continued from {a.handle}, carrying 1 monitor" in lines(b)


async def test_a_handoff_with_nothing_to_carry_says_so(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    r = await send(world, a, "/handoff go on")
    b = world.session(r["log_id"])
    assert f"continued in {b.handle}, nothing to carry" in lines(a)


async def test_autohandoff_and_autoclose_carry_over(world):
    a = await world.spawn()
    await turn(a, "earlier work")
    await send(world, a, "/autohandoff 200000")
    await send(world, a, "/autoclose on")
    r = await send(world, a, "/handoff go on")
    b = world.session(r["log_id"])
    assert b.autohandoff == 200000 and b.autoclose
    said = f"continued from {a.handle}, carrying autohandoff from 200k, autoclose"
    assert said in lines(b)


async def test_the_autohandoff_shows_when_it_fires(world):
    a = await world.spawn()
    world.app.sessions.rename(a.log_id, a.handle, "A named session")
    await send(world, a, "/autohandoff 500")
    brief = mcp("turn_end", attention="review", line="x", handoff="Done: X.")
    await turn(a, brief)
    await until(lambda: successor(world, a) is not None, 8, "the new session")
    assert any(
        x.startswith("autohandoff at ") and x.endswith("(from 500)") for x in lines(a)
    ), lines(a)
