"""turn_end done is accepted and names what the session left in its repos
(#977): the agent finishes it or ignores it, and nothing is refused."""

from aegis.agent_ops import LEFT_LINE

from .test_agents import turn, world  # noqa: F401
from .test_turn_end_seen import last_turn_end_reply, named, script


async def test_done_lists_the_pending_repo_work(world, monkeypatch):  # noqa: F811
    s = await named(world)
    left = ["worktree wt (topic) is still there", "1 unpushed commit(s) in repo (main)"]
    monkeypatch.setattr(world.app.repos, "pending", lambda log_id: left)
    await turn(s, script(["turn_end", {"attention": "done", "line": "did it"}]))
    reply = last_turn_end_reply(s)
    assert reply.endswith(LEFT_LINE.format(items="- " + left[0] + "\n- " + left[1])), (
        reply
    )
    assert s.wire()["attention"] == "done"


async def test_done_with_nothing_left_and_review_say_nothing_more(
    world,  # noqa: F811
    monkeypatch,
):
    s = await named(world)
    asked = []
    monkeypatch.setattr(
        world.app.repos, "pending", lambda log_id: asked.append(log_id) or []
    )
    await turn(s, script(["turn_end", {"attention": "done", "line": "did it"}]))
    assert "Accepted" not in last_turn_end_reply(s) and asked == [s.log_id]
    await turn(s, script(["turn_end", {"attention": "review", "line": "a step"}]))
    assert "Accepted" not in last_turn_end_reply(s) and asked == [s.log_id]
