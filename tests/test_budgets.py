"""Per-agent budgets (#1068): the budget: key, and spend charged by lineage."""

from pathlib import Path

import pytest

from decimal import Decimal

from aegis import budgets
from aegis.agents import load_agents
from aegis.budgets import Cap
from aegis.doctor import _agent as doctor_row
from aegis.guards import LINKS
from aegis.journal import db
from aegis.usage import tables
from aegis.usage.turns import Derived, TurnRow

PRESET = "{harness: claude-code, model: opus, effort: high, permission: full"


def agent(tmp_path: Path, budget: str):
    (tmp_path / ".aegis.yaml").write_text(
        f"agents:\n  a: {PRESET}, budget: {budget}}}\n"
    )
    (a,) = load_agents(tmp_path)
    return a


def test_an_agent_without_budget_has_no_cap(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(f"agents:\n  a: {PRESET}}}\n")
    (a,) = load_agents(tmp_path)
    assert a.budget == () and a.error is None


def test_a_budget_reads_its_caps_and_doctor_shows_them(tmp_path):
    a = agent(tmp_path, "[{usd: 2, per: session}, {tokens: 2000000, per: 24h}]")
    assert a.error is None
    assert a.budget == (Cap("usd", 2.0, "session"), Cap("tokens", 2e6, "24h"))
    assert [c.span_s for c in a.budget] == [None, 86400]
    (row,) = doctor_row(a, None)
    assert row.level == "ok"
    assert row.message.endswith("; budget: $2 per session, 2,000,000 tokens per 24h")


@pytest.mark.parametrize(
    "budget, says",
    [
        ("{usd: 2}", "budget must be a list"),
        ("[3]", "is not a map"),
        ("[{usd: 2, tokens: 9, per: 7d}]", "names one of usd or tokens"),
        ("[{per: 7d}]", "names one of usd or tokens"),
        ("[{usd: 2, per: 7d, every: 1}]", "not every"),
        ("[{usd: 0, per: 7d}]", "budget usd 0 is not a number above 0"),
        ("[{tokens: 1.5, per: 7d}]", "is not a whole number above 0"),
        ("[{usd: 2, per: week}]", "budget per 'week' is session or a duration"),
        ("[{usd: 2}]", "budget per None"),
    ],
)
def test_a_wrong_budget_stops_the_agent(tmp_path, budget, says):
    a = agent(tmp_path, budget)
    assert a.error is not None and says in a.error and not a.enabled
    (row,) = doctor_row(a, None)
    assert row.level == "error" and says in row.message


# The sessions, as their records (App._budget_record): fair's session F
# enqueued worker W on a deepseek queue; W spawned C; G is another session of
# fair, O one of opus; N, of opus, is a handoff of F onto another agent.
RECORDS = {
    "F": {"log_id": "F", "agent": "fair", "created_at": 0.0},
    "W": {"log_id": "W", "agent": "deepseek", "started_by": "F", "created_at": 0.0},
    "C": {"log_id": "C", "agent": "deepseek", "spawned_by": "W", "created_at": 0.0},
    "G": {"log_id": "G", "agent": "fair", "created_at": 0.0},
    "O": {"log_id": "O", "agent": "opus", "created_at": 0.0},
    "N": {"log_id": "N", "agent": "opus", "continues": "F", "created_at": 0.0},
}
H = 3600.0
NOW = 100 * H


class Usage:
    """journal.db's usage rows, each turn written with its session's agent
    and nearest starter, as the journal writes them."""

    def __init__(self, tmp_path: Path) -> None:
        self.path = tmp_path / "journal.db"
        self.con, _ = db.connect(self.path)
        tables.ensure(self.con)
        tables.complete_if(self.con, 0)  # ensure marks backfill 0: done
        self.rec = 0

    def add(self, log_id: str, ts: float, usd: float = 0.0, tokens: int = 0,
            agent: str | None = None, starter: str | None = None) -> None:  # fmt: skip
        r = RECORDS.get(log_id, {})
        starter = starter or next((r[k] for k in LINKS if r.get(k)), None)
        self.rec += 1
        t = TurnRow(self.rec, ts, "", "worker", "claude-code", "sonnet", "queue",
                    "api", Decimal(str(usd)), "billed", tokens, 0, 0, 0, 1.0, 0, 0,
                    0, 0, 0, None, agent=agent or r.get("agent"),
                    starter=starter)  # fmt: skip
        tables.insert(self.con, log_id, Derived(turns=[t]))

    def over(self, log_id: str, caps: dict, now: float = NOW, lookup=RECORDS.get):
        led = budgets.Ledger(self.path, lambda: False)
        own = budgets.line(RECORDS[log_id], log_id, lookup)
        return led.over(budgets.keys(own, caps), lambda k: None, now)


@pytest.fixture
def usage(tmp_path):
    u = Usage(tmp_path)
    yield u
    u.con.close()


def test_a_workers_turns_count_for_its_enqueuers_session_and_agent(usage):
    for lid, ts, usd in (("F", 5, 0.5), ("W", 4, 1.0), ("C", 3, 0.6), ("O", 2, 9.0)):
        usage.add(lid, NOW - ts, usd)
    session = {"fair": (Cap("usd", 2.0, "session"),)}
    o = usage.over("F", session)
    assert o is not None and o.session == "F" and o.spent == pytest.approx(2.1)
    assert o.frees_at is None  # a session cap never frees
    # The worker is past its starter's cap too: the chain reaches F.
    assert usage.over("C", session).session == "F"
    # Another session of fair has its own session cap, untouched.
    assert usage.over("G", session) is None
    # A window sums every session of fair and their descendants: F, W, C.
    window = {"fair": (Cap("usd", 2.5, "7d"),)}
    assert usage.over("G", window) is None
    usage.add("G", NOW - 1, 0.5)
    o = usage.over("G", window)
    assert o.agent == "fair" and o.spent == pytest.approx(2.6) and o.session is None
    # The worker's own agent carries no cap; opus's spend counts for no one.
    assert usage.over("O", window) is None


def test_a_handoff_onto_another_agent_stays_on_the_old_line(usage):
    """A capped agent cannot escape its cap through session_handoff(agent=...):
    the new session's spend counts for the old one's caps, and its line
    holds them."""
    usage.add("F", NOW - 5, 0.5)
    usage.add("N", NOW - 4, 1.0)
    session = {"fair": (Cap("usd", 1.2, "session"),)}
    assert usage.over("F", session).spent == pytest.approx(1.5)
    o = usage.over("N", session)
    assert o is not None and o.session == "F" and o.agent == "fair"


def test_a_session_aegis_no_longer_knows_still_charges_its_starter(usage):
    """W was archived and purged: its rows still name its agent and its
    starter, so C, spawned by W, still charges F."""
    usage.add("F", NOW - 5, 0.5)
    usage.add("W", NOW - 4, 0.5)
    usage.add("C", NOW - 3, 0.5)
    led = budgets.Ledger(usage.path, lambda: False)
    known = {k: v for k, v in RECORDS.items() if k != "W"}
    lookup = lambda lid: known.get(lid) or led.record(lid)  # noqa: E731
    assert budgets.line(RECORDS["C"], "C", lookup) == [
        ("C", "deepseek"), ("W", "deepseek"), ("F", "fair")
    ]  # fmt: skip
    session = {"fair": (Cap("usd", 1.2, "session"),)}
    assert usage.over("C", session, lookup=lookup).spent == pytest.approx(1.5)
    assert led.record("nobody") is None


def test_a_24h_window_frees_when_its_oldest_spend_ages_out(usage):
    cap = {"fair": (Cap("tokens", 1000, "24h"),)}
    usage.add("F", NOW - 20 * H, tokens=600)
    usage.add("F", NOW - 2 * H, tokens=300)
    usage.add("W", NOW - H, tokens=200)
    o = usage.over("F", cap)
    assert o.spent == 1100
    # Without its oldest row, 500 is left: under the cap.
    assert o.frees_at == NOW - 20 * H + 24 * H
    assert usage.over("F", cap, now=o.frees_at) is None
    assert usage.over("F", cap, now=o.frees_at - 1) is not None


def test_the_cap_that_frees_last_names_the_gate(usage):
    caps = {
        "fair": (Cap("usd", 1, "24h"), Cap("usd", 1, "7d"), Cap("usd", 3, "session"))
    }
    usage.add("F", NOW - 30 * H, 1.0)
    usage.add("F", NOW - H, 1.0)
    o = usage.over("F", caps)
    assert o.cap.per == "7d" and o.frees_at == NOW - H + 7 * 24 * H
    usage.add("F", NOW - 1, 1.0)
    assert usage.over("F", caps).cap.per == "session"


def test_a_cap_is_read_once_and_kept_until_a_window_frees(usage):
    usage.add("F", NOW - H, 1.0)
    led = budgets.Ledger(usage.path, lambda: False)
    caps = {"fair": (Cap("usd", 1, "24h"), Cap("usd", 5, "session"))}
    ks = budgets.keys(budgets.line(RECORDS["F"], "F", RECORDS.get), caps)
    o = led.over(ks, lambda k: None, NOW)
    assert o.cap.per == "24h" and led.reads == 2
    # A queue task's line (no log id) reads the same window: kept.
    assert led.over(budgets.keys([(None, "fair")], caps), lambda k: None, NOW) == o
    assert led.reads == 2
    # Past the time it frees, the window alone is read again.
    assert led.over(ks, lambda k: None, o.frees_at) is None and led.reads == 3


def test_while_usage_is_rebuilt_what_is_kept_stands(usage):
    usage.add("F", NOW - H, 1.0)
    busy = [False]
    led = budgets.Ledger(usage.path, lambda: busy[0])
    caps = {"fair": (Cap("usd", 1, "7d"),)}
    ks = budgets.keys([("F", "fair")], caps)
    busy[0] = True
    # Nothing kept: the capped line stays closed, by its agent.
    assert led.over(ks, lambda k: None, NOW) == "fair" and led.reads == 0
    busy[0] = False
    o = led.over(ks, lambda k: None, NOW)
    assert o is not None
    usage.con.execute("DELETE FROM usage_turns")  # a rebuild cleared it
    usage.con.commit()
    busy[0] = True
    assert led.over(ks, lambda k: None, NOW) == o
    # Past the time it frees, it is still what was kept until usage is back.
    assert led.over(ks, lambda k: None, o.frees_at + 1) == o and led.reads == 1
    # A rebuild in another process: the file's own backfill mark says so.
    busy[0] = False
    tables.clear(usage.con, 5)
    assert led.over(ks, lambda k: None, o.frees_at + 1) == o
    assert (
        budgets.Ledger(usage.path, lambda: False).over(ks, lambda k: None, NOW)
        == "fair"
    )


def test_a_read_is_bounded_by_the_line_not_the_server(usage):
    """10,000 turns of other agents, all inside the window, cost the read of
    one capped line (nearly) nothing: it is filtered in SQL by lineage."""
    for k in range(5):
        usage.add("F", NOW - k, 0.1)
        usage.add("W", NOW - k, 0.1)
    caps = {"fair": (Cap("usd", 100, "7d"), Cap("usd", 100, "session"))}
    ks = budgets.keys([("F", "fair")], caps)

    def steps() -> int:
        con = db.open_read(usage.path)
        n = [0]

        def tick() -> int:
            n[0] += 1
            return 0

        con.set_progress_handler(tick, 100)
        try:
            for k in ks:
                budgets.measure(con, k, None, NOW)
        finally:
            con.close()
        return n[0]

    before = steps()
    usage.con.executemany(
        "INSERT INTO usage_turns VALUES(?,1,?,'','worker','claude-code','opus',"
        "'default','api',0.5,'billed',1,1,0,0,1.0,0,0,0,0,0,NULL,'opus',NULL,?)",
        [(f"X{i}", NOW - i % 1000, f"X{i - 1}" if i % 10 else None)
         for i in range(10_000)],
    )  # fmt: skip
    usage.con.commit()
    assert usage.con.execute("SELECT count(*) FROM usage_turns").fetchone()[0] == 10_010
    after = steps()
    assert after <= 2 * before + 20, (before, after)


@pytest.mark.parametrize(
    "budget, says",
    [
        ("[{usd: .inf, per: 7d}]", "budget usd inf is not a number above 0"),
        ("[{usd: .nan, per: 7d}]", "budget usd nan is not a number above 0"),
        ("[{usd: 2, per: 367d}]", "budget per '367d' is longer than 366d"),
        ("[{usd: 2, per: 9000h}]", "budget per '9000h' is longer than 366d"),
    ],
)
def test_a_budget_refuses_infinite_amounts_and_absurd_windows(tmp_path, budget, says):
    a = agent(tmp_path, budget)
    assert a.error is not None and says in a.error


def test_a_366d_window_is_the_longest(tmp_path):
    assert agent(tmp_path, "[{usd: 2, per: 366d}]").error is None


def test_the_notice_says_what_was_spent_and_until_when():
    o = budgets.Over("fair", Cap("usd", 25, "7d"), 25.4, None, NOW)
    g = budgets.gate(o, "F", str)
    assert g.state == "closed" and budgets.is_budget(g)
    assert not budgets.never_frees(g)
    assert g.why == "agent fair has spent $25.40 of $25 in the last 7d"
    text = budgets.notice(g)
    assert text.startswith(
        "agent fair has spent $25.40 of $25 in the last 7d. Finish what you are"
        " doing as soon as possible and end your turn. aegis starts no turn here"
        " that a person did not start until it has room again, at "
    )
    assert budgets.again(o) == "agent fair's 7d budget has room again."
    s = budgets.Over("fair", Cap("tokens", 1000, "session"), 1200, "F", None)
    assert budgets.gate(s, "F", str).why == (
        "agent fair has spent 1,200 of 1,000 tokens in this session"
    )
    assert budgets.gate(s, "W", lambda lid: "fair-1").why.endswith("in session fair-1")
    assert "a session cap never frees" in budgets.notice(budgets.gate(s, "F", str))
    assert budgets.never_frees(budgets.gate(s, "F", str))


# Against a running aegis: the fake claude's every turn costs $0.01.
import asyncio  # noqa: E402

from .conftest import until  # noqa: E402
from .test_agents import World, mcp, turn  # noqa: E402
from .test_guards import sent  # noqa: E402

CAPPED = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  fair:
    harness: claude-code
    model: sonnet
    effort: low
    permission: full
    budget: [{usd: %s, per: session}]
queues:
  fair-work:
    agent: opus
    quota:
      claude: {weekly_all: {used: 99}}
      on_closed: reject
  fair-hold: {agent: opus}
  fair-later: {agent: opus, priority: 0}
"""
KEY = "budget:spent:fair:session"


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CAPPED % "0.01")
    w = await World(tmp_path, fake_claude).start()
    w.app.quota.snapshot = lambda: {"providers": []}  # no reading: no guard holds
    yield w
    await w.stop()


async def spawn(world, agent: str = "fair"):
    r = await world.app.registry.call("session.spawn", {"agent": agent})
    return world.app.sessions.sessions[r["log_id"]]


def owed(world, s) -> dict:
    return world.app.notices._items.get(s.log_id, {})


async def test_past_its_session_cap_a_session_is_told_once_and_holds_a_wake(world):
    s = await spawn(world)
    await turn(s, "hello")
    await until(lambda: KEY in owed(world, s), 8, "the budget notice")
    assert owed(world, s)[KEY].text.startswith(
        "agent fair has spent $0.01 of $0.01 in this session. Finish what you"
        " are doing as soon as possible and end your turn."
    )
    # A wake no person started is held, whatever on_closed says.
    assert await s.deliver("> from feed:tickets · 1 new", "ticket 7") == "held"
    assert s.held and s.held[0]["held_for"] == "quota" and s.status == "idle"
    # A person's message runs, and carries the notice.
    await turn(s, "a person asks", by_person=True)
    assert KEY in s.items_sent
    await turn(s, "and again", by_person=True)
    rows = [e for e in s.entries() if ":notice:" in e["id"]]
    assert [e["title"].split(" · ")[1] for e in rows] == [KEY]
    assert s.held and not any("ticket 7" in t for t in sent(s))


async def test_a_cap_with_room_again_releases_what_it_held(world, tmp_path):
    s = await spawn(world)
    await turn(s, "hello")
    await until(lambda: KEY in owed(world, s), 8, "the budget notice")
    assert await s.deliver("> from feed:tickets · 1 new", "ticket 7") == "held"
    (tmp_path / ".aegis.yaml").write_text(CAPPED % "5")
    await until(
        lambda: world.app.config.current().agents[1].budget[0].limit == 5, 5, "config"
    )
    world.app.on_quota(world.app.quota.snapshot())
    await until(lambda: not s.held and s.status == "idle", 8, "the held wake")
    assert any("ticket 7" in t for t in sent(s))
    assert KEY not in owed(world, s)


async def test_a_workers_spend_closes_its_enqueuer_and_its_queue_rejects(
    world, tmp_path
):
    (tmp_path / ".aegis.yaml").write_text(CAPPED % "0.015")
    await until(
        lambda: world.app.config.current().agents[1].budget[0].limit == 0.015,
        5,
        "config",
    )
    s = await spawn(world)
    said = await turn(s, mcp("queue_enqueue", queue="fair-work", payload="hi"))
    assert "mcp error" not in said
    (task,) = world.app.queues.tasks.values()
    await until(lambda: task.status == "completed", 12, "the worker")
    # fair spent $0.01, its worker $0.01: past fair's $0.015 session cap.
    await until(lambda: KEY in owed(world, s), 8, "the budget notice")
    said = await turn(
        s, mcp("queue_enqueue", queue="fair-work", payload="more"), by_person=True
    )
    assert said.startswith("mcp error: queue_closed")
    assert "agent fair has spent $0.0" in said and "of $0.015 in session" in said
    await asyncio.sleep(0)


async def test_a_spent_session_cap_closes_a_hold_queue_and_fails_its_pending(
    world, tmp_path
):
    """A session cap never frees: an enqueue from its line is refused
    whatever the queue's on_closed, and a task already pending from it
    fails with the why rather than waiting forever."""
    (tmp_path / ".aegis.yaml").write_text(CAPPED % "0.015")
    await until(
        lambda: world.app.config.current().agents[1].budget[0].limit == 0.015,
        5,
        "config",
    )
    s = await spawn(world)
    said = await turn(s, mcp("queue_enqueue", queue="fair-later", payload="hi"))
    assert "mcp error" not in said
    (task,) = world.app.queues.tasks.values()
    assert task.status == "pending"  # priority 0: it waits
    await turn(s, "hello")  # $0.02 of $0.015
    await until(lambda: task.status == "failed", 8, "the pending task fails")
    assert "agent fair has spent $0.02 of $0.015" in task.error
    assert task.error.endswith("a session cap never frees")
    said = await turn(
        s, mcp("queue_enqueue", queue="fair-hold", payload="more"), by_person=True
    )
    assert said.startswith("mcp error: queue_closed")
    assert "a session cap never frees" in said
    assert len(world.app.queues.tasks) == 1


async def test_a_rebuild_neither_clears_nor_continues(world):
    """While usage is rebuilt the rows are not all there: what the ledger
    kept stands, a cap with nothing kept stays closed, and nothing is told
    or continued."""
    s = await spawn(world)
    await turn(s, "hello")
    await until(lambda: KEY in owed(world, s), 8, "the budget notice")
    assert await s.deliver("> from feed:tickets · 1 new", "ticket 7") == "held"
    world.app.journal._progress = {"done": 0, "total": 3}
    try:
        kept = dict(world.app.ledger.kept)
        world.app.ledger.kept = {}
        g = world.app.session_guard(s)
        assert g.state == "closed" and g.why == budgets.REBUILDING
        assert not budgets.never_frees(g)
        world.app.on_quota(world.app.quota.snapshot())
        world.app._usage_recorded()
        assert world.app._budget_task is None or world.app._budget_task.done()
        await asyncio.sleep(0.2)
        assert s.held and KEY in owed(world, s)
        world.app.ledger.kept = kept
        assert world.app.session_guard(s).why.startswith("agent fair has spent")
    finally:
        world.app.journal._progress = {}
    world.app.on_quota(world.app.quota.snapshot())
    await asyncio.sleep(0.2)
    assert s.held and KEY in owed(world, s)
