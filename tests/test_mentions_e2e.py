"""@ mentions end to end: real sessions on the fake claude, the real app."""

import pytest

from aegis import mentions
from aegis.ops import OpError

from .conftest import until
from .test_agents import CONFIG, World, inbox, mcp, turn


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def say(world, a, text):
    return await world.app.registry.call(
        "session.send", {"log_id": a.log_id, "text": text}
    )


def mention(a):
    (e,) = [e for e in a.entries() if e["kind"] == "mention"]
    return e


def prose(s):
    return [e for e in s.entries() if e["kind"] == "prose"]


async def test_a_note_lands_in_the_other_session_and_not_here(world):
    a, b = await world.spawn(), await world.spawn()
    r = await say(world, a, f"@{b.handle} the flaky test is in test_tabs.py")
    assert r["members"] == [{"name": b.handle, "kind": "session", "state": "asked"}]
    await until(lambda: inbox(b), what="the note")
    (note,) = inbox(b)
    assert note["title"].startswith("user")
    assert f"A note typed in {a.handle} (mention#{r['mention']})" in note["md"]
    assert "the flaky test is in test_tabs.py" in note["md"]
    assert f"mention_reply id={r['mention']}" in note["md"]
    e = mention(a)
    assert (
        e["status"] == "ok"
        and e["detail"]["body"] == "the flaky test is in test_tabs.py"
    )
    assert a.status == "idle" and prose(a) == []


async def test_a_note_to_a_busy_session_is_held(world):
    a, b = await world.spawn(), await world.spawn()
    await b.send("/busy 2")
    await until(lambda: b.status == "working", what="working")
    r = await say(world, a, f"@{b.handle} when you can")
    assert r["members"][0]["state"] == "held"
    assert mention(a)["detail"]["members"][0]["state"] == "held"
    await until(lambda: inbox(b), timeout=10, what="the held note")


async def test_an_agent_run_answers_into_the_tab_and_rides_the_next_message(world):
    a = await world.spawn()
    await turn(a, "first ask")
    r = await say(world, a, "@reviewer is this migration safe?")
    assert r["members"] == [{"name": "reviewer", "kind": "agent", "state": "asked"}]
    await until(lambda: mention(a)["status"] == "ok", timeout=12, what="the answer")
    (m,) = mention(a)["detail"]["members"]
    assert m["state"] == "answered"
    assert "Question: is this migration safe?" in m["answer"]
    assert f"peer_read {a.handle}" in m["answer"]
    assert a.status == "idle"  # the answer started no turn
    await say(world, a, "ok, go on")
    await until(lambda: mention(a)["detail"]["carried"], what="the carry")
    await until(lambda: a.status == "idle", timeout=10, what="the turn")
    seen = await turn(a, "/recall")
    assert (
        'reviewer answered the person\'s @ mention ("is this migration safe?")' in seen
    )
    # The person's row shows what they typed, not the quote.
    users = [e for e in a.entries() if e["kind"] == "user"]
    assert any(e["md"] == "ok, go on" for e in users)


async def test_a_committee_merges_once_every_member_answered(world):
    a, b = await world.spawn(), await world.spawn()
    r = await say(world, a, f"@reviewer @{b.handle} review this plan")
    mid = r["mention"]
    await until(
        lambda: mention(a)["detail"]["members"][0]["state"] == "answered",
        timeout=12,
        what="the agent's answer",
    )
    assert not [e for e in inbox(a) if e["title"].startswith("committee")]
    await until(lambda: inbox(b), what="the note")
    assert "A committee waits for your answer" in inbox(b)[0]["md"]
    await turn(b, mcp("mention_reply", id=mid, text="looks right to me"))
    await until(
        lambda: [e for e in inbox(a) if e["title"].startswith("committee")],
        timeout=10,
        what="the merge",
    )
    (merge,) = [e for e in inbox(a) if e["title"].startswith("committee")]
    assert f"mention#{mid} · 2 of 2 answered" in merge["title"]
    assert f"## {b.handle}\nlooks right to me" in merge["md"]
    assert mention(a)["detail"]["merge"] == "sent"


async def test_a_skipped_merge_never_goes_out(world):
    a, b = await world.spawn(), await world.spawn()
    r = await say(world, a, f"@reviewer @{b.handle} review this plan")
    assert (
        await world.app.registry.call(
            "mention.skip", {"log_id": a.log_id, "id": r["mention"]}
        )
    )["status"] == "skipped"
    await until(lambda: inbox(b), what="the note")
    await turn(b, mcp("mention_reply", id=r["mention"], text="fine"))
    await until(
        lambda: mention(a)["detail"]["members"][1]["state"] == "answered",
        what="the answer",
    )
    assert not [e for e in inbox(a) if e["title"].startswith("committee")]
    assert mention(a)["detail"]["merge"] == "skipped"


async def test_a_silent_member_does_not_hold_the_committee(world, monkeypatch):
    monkeypatch.setattr(mentions, "MERGE_AFTER_S", 1)
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    r = await say(world, a, f"@{b.handle} @{c.handle} anyone?")
    await until(
        lambda: [e for e in inbox(a) if e["title"].startswith("committee")],
        timeout=8,
        what="the merge at the wait's end",
    )
    (merge,) = [e for e in inbox(a) if e["title"].startswith("committee")]
    assert f"mention#{r['mention']} · 0 of 2 answered" in merge["title"]
    assert "(no answer within the wait)" in merge["md"]


async def test_what_is_refused_and_what_stays_text(world):
    a, b = await world.spawn(), await world.spawn()
    for line, code in [
        ("@nobody-here hi", "no_session"),
        (f"@{a.handle} hi", "bad_mention"),
        (f"@{b.handle}", "bad_mention"),
        ("@!reviewer hurry", "bad_mention"),
    ]:
        with pytest.raises(OpError) as e:
            await say(world, a, line)
        assert e.value.code == code, line
    assert [e for e in a.entries() if e["kind"] == "mention"] == []
    await say(world, a, "@@reviewer is a literal")
    await until(lambda: prose(a), what="the reply")
    assert any(
        e["kind"] == "user" and e["md"] == "@reviewer is a literal" for e in a.entries()
    )
    assert "You said: **@reviewer is a literal**" in prose(a)[0]["md"]


async def test_only_an_asked_session_may_reply(world):
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    r = await say(world, a, f"@{b.handle} what do you think?")
    said = await turn(c, mcp("mention_reply", id=r["mention"], text="me too"))
    assert "not_asked" in said
    said = await turn(b, mcp("mention_reply", id=r["mention"], text="I think so"))
    assert "answered" in said
    (m,) = mention(a)["detail"]["members"]
    assert m["state"] == "answered" and m["answer"] == "I think so"
