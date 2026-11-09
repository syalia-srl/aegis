"""Feeds: a growing source wakes its session with each batch (#998)."""

import asyncio
import json
import shutil

import pytest

from aegis.feeds import (
    BATCH_BYTES,
    Feed,
    Feeds,
    batch_text,
    command_script,
    run_jq,
    split_lines,
    take,
)
from aegis.monitors import run_bash

from .conftest import until

FAST = {"interval_s": 0.1}


# -- the pieces ---------------------------------------------------------------
def test_a_line_without_its_newline_waits():
    assert split_lines(b"a\nb\nhalf") == (["a", "b"], 4)
    assert split_lines(b"half") == ([], 0)


def test_a_batch_stops_at_the_cap_or_the_byte_limit_but_takes_one():
    items = [{"text": "x" * 10, "seen": 0.0}] * 80
    assert len(take(items, 50)) == 50
    big = [{"text": "x" * (BATCH_BYTES // 3), "seen": 0.0}] * 5
    assert len(take(big, 50)) == 2
    huge = [{"text": "x" * (BATCH_BYTES * 2), "seen": 0.0}]
    assert len(take(huge, 50)) == 1


def test_the_message_names_the_batch_and_what_still_waits():
    f = Feed(
        name="chat", owner="o", cwd="/", source={"file": "c.jsonl", "format": "jsonl"}
    )
    f.batch = 42
    f.pending = [{"text": "{}", "seen": 0.0}] * 3
    header, body = batch_text(f, [{"text": '{"a":1}', "seen": 95.0}], 100.0, again=True)
    assert header.startswith("> from feed:chat · batch#42 again · 1 item · ")
    assert '```jsonl\n{"a":1}\n```' in body
    assert "3 more items wait and come when this turn ends." in body
    assert "the oldest seen 5s ago" in body


async def test_a_command_gets_its_cursor_and_hands_back_the_next(tmp_path):
    f = Feed(name="n", owner="o", cwd=str(tmp_path), source={"command": (
        'echo "got:$AEGIS_FEED_CURSOR"; echo 7 > "$AEGIS_FEED_NEXT"'
    )}, pos={"cursor": "3"})  # fmt: skip
    rc, out, _ = await run_bash(command_script(f), str(tmp_path))
    assert rc == 0 and out.startswith("got:3\n") and out.endswith("\x1e7\n")


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
async def test_jq_answers_once_per_line_even_when_it_drops_one():
    got = await run_jq("select(.t) | .t", [{"t": "a"}, {"x": 1}, {"t": "b"}])
    assert got == [["a"], [], ["b"]]


# -- a feed against a stand-in registry ---------------------------------------
class FakeSession:
    def __init__(self, log_id="s1"):
        self.log_id = log_id
        self.held: list[dict] = []
        self.in_turn = False
        self.got: list[tuple[str, str]] = []

    async def deliver(self, header, body, through_lock=False):
        self.got.append((header, body))
        return "sent"

    def _hold(self, msg):
        self.held.append(msg)


class FakeRegistry:
    def __init__(self, *sessions):
        self.sessions = {s.log_id: s for s in sessions}
        self.notices = None

    def refresh_card(self, log_id):
        pass


async def _feeds(tmp_path, *sessions):
    s = sessions or (FakeSession(),)
    reg = FakeRegistry(*s)
    return Feeds(reg, tmp_path / "feeds.json"), reg, s[0]


async def test_new_lines_go_as_one_batch_after_the_debounce(tmp_path):
    log = tmp_path / "chat.log"
    log.write_text("old\n")
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start(
            "s1", str(tmp_path), "chat", {"file": "chat.log", **FAST}, debounce_s=0.3
        )
        with log.open("a") as fh:
            fh.write("one\ntwo\nhal")
        await asyncio.sleep(0.15)
        with log.open("a") as fh:
            fh.write("f\n")
        await until(lambda: s.got, timeout=3, what="the batch")
        header, body = s.got[0]
        assert "batch#1 · 3 items" in header
        assert "```text\none\ntwo\nhalf\n```" in body and "\nold\n" not in body
        await asyncio.sleep(0.4)
        assert len(s.got) == 1
    finally:
        await feeds.shutdown()


async def test_a_working_session_gets_one_block_when_its_turn_ends(tmp_path):
    log = tmp_path / "chat.log"
    log.write_text("")
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start(
            "s1", str(tmp_path), "chat", {"file": "chat.log", **FAST}, debounce_s=0.1
        )
        s.in_turn = True
        for i in range(3):
            with log.open("a") as fh:
                fh.write(f"m{i}\n")
            await asyncio.sleep(0.25)
        assert not s.got and not s.held, "nothing goes mid-turn"
        assert len(feeds.get("s1", "chat").pending) == 3
        s.in_turn = False
        s.held.append({"header": "> from monitor:m · ok · x", "body": "", "ts": 0})
        feeds.turn_ended(s)  # a flush is pending: the block joins it
        assert [m["header"].split(" · ")[0] for m in s.held] == [
            "> from monitor:m",
            "> from feed:chat",
        ]
        assert "m0\nm1\nm2" in s.held[1]["body"]
    finally:
        await feeds.shutdown()


async def test_past_the_cap_the_rest_waits_for_the_next_turn(tmp_path):
    log = tmp_path / "chat.log"
    log.write_text("")
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start("s1", str(tmp_path), "chat", {"file": "chat.log", **FAST},
                          debounce_s=0.1, max_items=2)  # fmt: skip
        s.in_turn = True
        log.write_text("a\nb\nc\nd\ne\n")
        await until(lambda: len(feeds.get("s1", "chat").pending) == 5, what="the read")
        s.in_turn = False
        for n in (2, 2, 1):
            feeds.turn_ended(s)
            await until(lambda: s.got, what="a batch")
            assert f"· {n} item" in s.got.pop()[0]
    finally:
        await feeds.shutdown()


async def test_a_truncated_file_is_read_from_its_start(tmp_path):
    log = tmp_path / "chat.log"
    log.write_text("a\nb\n")
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start(
            "s1", str(tmp_path), "chat", {"file": "chat.log", **FAST}, debounce_s=0.1
        )
        log.write_text("z\n")
        await until(lambda: s.got, timeout=3, what="the batch")
        assert "read from its start" in s.got[0][1] and "\nz\n" in s.got[0][1]
    finally:
        await feeds.shutdown()


async def test_from_a_line_starts_after_it(tmp_path):
    (tmp_path / "c.log").write_text("a\nb\nc\n")
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start("s1", str(tmp_path), "c", {"file": "c.log", "from": {"line": 1}, **FAST},
                          debounce_s=0.1)  # fmt: skip
        await until(lambda: s.got, timeout=3, what="the batch")
        assert "```text\nb\nc\n```" in s.got[0][1]
        assert feeds.get("s1", "c").pos["line"] == 3
    finally:
        await feeds.shutdown()


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is not installed")
async def test_a_filter_drops_lines_and_shapes_items(tmp_path):
    log = tmp_path / "c.jsonl"
    log.write_text("")
    feeds, _, s = await _feeds(tmp_path)
    try:
        src = {
            "file": "c.jsonl",
            "format": "jsonl",
            "filter": "select(.text) | {from, text}",
            **FAST,
        }
        await feeds.start("s1", str(tmp_path), "c", src, debounce_s=0.1)
        rows = [{"from": "ana", "text": "hi", "id": 1}, {"from": "ben", "joined": True},
                {"from": "cara", "text": "yo", "id": 3}]  # fmt: skip
        log.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
        await until(lambda: s.got, timeout=3, what="the batch")
        assert (
            '{"from":"ana","text":"hi"}\n{"from":"cara","text":"yo"}\nnot json'
            in s.got[0][1]
        )
        assert feeds.get("s1", "c").filtered == 1
    finally:
        await feeds.shutdown()


async def test_a_command_feed_moves_its_cursor(tmp_path):
    cmd = (
        'n=${AEGIS_FEED_CURSOR:-0}; [ "$n" -ge 2 ] && exit 1; '
        'echo "item $n"; echo $((n + 1)) > "$AEGIS_FEED_NEXT"'
    )
    feeds, _, s = await _feeds(tmp_path)
    try:
        await feeds.start(
            "s1", str(tmp_path), "n", {"command": cmd, **FAST}, debounce_s=0.3
        )
        await until(lambda: s.got, timeout=4, what="the batch")
        assert "item 0\nitem 1" in s.got[0][1]
        assert feeds.get("s1", "n").pos["cursor"] == "2"
    finally:
        await feeds.shutdown()


async def test_three_failed_polls_pause_the_feed(tmp_path):
    feeds, _, s = await _feeds(tmp_path)
    try:
        f = await feeds.start("s1", str(tmp_path), "n", {"command": "exit 3", **FAST})
        await until(lambda: f.paused, timeout=3, what="the pause")
        assert f.paused.startswith("3 failed polls in a row: exit 3")
        feeds.resume(f)
        assert f.paused is None and f.errors == 0
    finally:
        await feeds.shutdown()


async def test_items_without_a_next_cursor_are_an_error(tmp_path):
    feeds, _, s = await _feeds(tmp_path)
    try:
        f = await feeds.start("s1", str(tmp_path), "n", {"command": "echo x", **FAST})
        assert "wrote no next cursor" in f.last_error and not f.pending
    finally:
        await feeds.shutdown()


async def test_a_missing_file_is_refused_at_start(tmp_path):
    from aegis.feeds import SourceError

    feeds, _, _ = await _feeds(tmp_path)
    with pytest.raises(SourceError, match="No such file"):
        await feeds.start("s1", str(tmp_path), "c", {"file": "nope.log"})
    assert not feeds.items


async def test_a_batch_handed_off_but_not_saved_goes_again_marked(tmp_path):
    (tmp_path / "c.log").write_text("")
    feeds, reg, s = await _feeds(tmp_path)
    await feeds.start(
        "s1", str(tmp_path), "c", {"file": "c.log", **FAST}, debounce_s=0.1
    )
    await feeds.shutdown()
    f = feeds.get("s1", "c")
    f.pending = [{"text": "x", "seen": 0.0}]
    f.sending = f.batch + 1  # the crash came between the hand-off and the save
    feeds._save()
    again = Feeds(reg, tmp_path / "feeds.json")
    again.boot()
    again._deliver(again.get("s1", "c"), s)
    await until(lambda: s.got, what="the batch")
    assert "batch#1 again" in s.got[0][0]
    assert again.get("s1", "c").sending is None


async def test_a_handoff_moves_the_feed_with_its_cursor(tmp_path):
    (tmp_path / "c.log").write_text("a\n")
    old, new = FakeSession("s1"), FakeSession("s2")
    feeds, _, _ = await _feeds(tmp_path, old, new)
    try:
        await feeds.start(
            "s1", str(tmp_path), "c", {"file": "c.log", **FAST}, debounce_s=0.1
        )
        feeds.move("s1", "s2")
        assert feeds.of("s1") == [] and feeds.get("s2", "c").pos["offset"] == 2
        with (tmp_path / "c.log").open("a") as fh:
            fh.write("b\n")
        await until(lambda: new.got, timeout=3, what="the batch in the new session")
        assert not old.got and "```text\nb\n```" in new.got[0][1]
    finally:
        await feeds.shutdown()


# -- in a real server ---------------------------------------------------------
async def test_a_feed_started_by_its_agent_wakes_it_and_holds_done(
    tmp_path, fake_claude
):
    from .test_agents import CONFIG, World, inbox, mcp, turn

    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        a = await w.spawn()
        log = tmp_path / "chat.log"
        log.write_text("seen\n")
        src = {"file": str(log), "from": {"line": 1}}
        reply = await turn(a, mcp("feed_start", name="chat", source=src, debounce_s=0))
        assert '"first_poll": "ok"' in reply, reply
        f = w.app.feeds.get(a.log_id, "chat")
        f.source["interval_s"] = 0.1
        w.app.feeds._wake(f)
        assert w.app.sessions.wakers(a) == ["1 feed"]
        refused = await turn(a, mcp("turn_end", attention="done", line="finished"))
        assert "feed" in refused, refused
        log.write_text("seen\nbug: the export button does nothing\n")
        await until(lambda: any("feed:chat" in e["title"] for e in inbox(a)), timeout=8,
                    what="the feed's wake")  # fmt: skip
        assert "the export button" in next(
            e["md"] for e in inbox(a) if "feed:chat" in e["title"]
        )
        await w.app.sessions.close(a.log_id)
        assert w.app.feeds.of(a.log_id) == []
    finally:
        await w.stop()


# -- the card's history -------------------------------------------------------
def test_the_card_counts_items_per_bucket_and_batches_as_ticks():
    from aegis.feeds import card_stats

    now = 1_800_000_000.0
    f = Feed(name="c", owner="o", cwd="/", source={"file": "c"})
    f.arrivals = [now - 30, now - 40, now - 3500]
    hour = int(now // 3600)
    f.hours = {str(hour): 2, str(hour - 1): 5, str(hour - 30): 4}
    f.batches = [{"b": 1, "at": now - 20, "n": 2, "shared": False, "turn_s": 30.0, "tokens": 900},
                 {"b": 2, "at": now - 7200, "n": 5, "shared": True, "turn_s": None, "tokens": None}]  # fmt: skip
    f.waits = [[now - 20, 10.0], [now - 20, 20.0], [now - 7200, 300.0]]
    f.acc = {"debounce": 30.0, "turn": 90.0}
    got = card_stats(f, now)
    assert (
        got["ranges"]["hour"]["counts"][-1] == 2
        and sum(got["ranges"]["hour"]["counts"]) == 3
    )
    assert got["ranges"]["day"]["counts"][-2:] == [5, 2]
    assert sum(got["ranges"]["week"]["counts"]) == 11
    assert got["ranges"]["hour"]["batches"][-1] == 1
    assert got["ranges"]["hour"]["wait"] == [20.0, 20.0, 20.0]
    assert got["ranges"]["day"]["wait"][2] == 300.0
    assert got["split"] == {"debounce": 0.25, "turn": 0.75}
    assert got["per_batch"] == {"items": 5, "turn_s": 30.0, "tokens": 900}


def test_waiting_is_charged_to_why_it_waited():
    f = Feed(name="c", owner="o", cwd="/", source={"file": "c"})
    s = FakeSession()
    f.account(s, 100.0)
    f.pending = [{"text": "a", "seen": 100.0}, {"text": "b", "seen": 100.0}]
    f.account(s, 110.0)  # idle: debounce
    s.in_turn = True
    f.account(s, 115.0)
    f.paused = "by you"
    f.account(s, 116.0)
    assert f.acc == {"debounce": 20.0, "turn": 10.0, "paused": 2.0}


async def test_a_batch_records_the_turn_it_started(tmp_path):
    (tmp_path / "c.log").write_text("")
    feeds, _, s = await _feeds(tmp_path)
    s.turn_tokens = [1200, 300]
    try:
        await feeds.start(
            "s1", str(tmp_path), "c", {"file": "c.log", **FAST}, debounce_s=0.1
        )
        (tmp_path / "c.log").write_text("a\n")
        await until(lambda: s.got, timeout=3, what="the batch")
        f = feeds.get("s1", "c")
        assert f.open_batch == 1 and f.batches[0]["shared"] is False
        feeds.turn_ended(s)
        assert f.batches[0]["tokens"] == 1500 and f.batches[0]["turn_s"] >= 0
        card = f.card(s)
        assert card["waiting"] == [] and card["recent"][0]["text"] == "a"
    finally:
        await feeds.shutdown()
