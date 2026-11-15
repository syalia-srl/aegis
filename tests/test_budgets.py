"""Per-agent budgets (#1068): the budget: key, and spend charged by lineage."""

from pathlib import Path

import pytest

from aegis import budgets
from aegis.agents import load_agents
from aegis.budgets import Cap, Row
from aegis.doctor import _agent as doctor_row

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
# fair, O one of opus.
RECORDS = {
    "F": {"log_id": "F", "agent": "fair", "created_at": 0.0},
    "W": {"log_id": "W", "agent": "deepseek", "started_by": "F", "created_at": 0.0},
    "C": {"log_id": "C", "agent": "deepseek", "spawned_by": "W", "created_at": 0.0},
    "G": {"log_id": "G", "agent": "fair", "created_at": 0.0},
    "O": {"log_id": "O", "agent": "opus", "created_at": 0.0},
}
H = 3600.0
NOW = 100 * H


def line_of(row: Row):
    return budgets.line(RECORDS[row.log_id], row.log_id, RECORDS.get)


def over(log_id: str, caps: dict, rows: list[Row], now: float = NOW):
    own = budgets.line(RECORDS[log_id], log_id, RECORDS.get)
    return budgets.over(own, caps, rows, line_of, now)


def test_a_workers_turns_count_for_its_enqueuers_session_and_agent():
    rows = [Row("F", NOW - 5, 0.5, 0), Row("W", NOW - 4, 1.0, 0),
            Row("C", NOW - 3, 0.6, 0), Row("O", NOW - 2, 9.0, 0)]  # fmt: skip
    session = {"fair": (Cap("usd", 2.0, "session"),)}
    o = over("F", session, rows)
    assert o is not None and o.session == "F" and o.spent == pytest.approx(2.1)
    assert o.frees_at is None  # a session cap never frees
    # The worker is past its starter's cap too: the chain reaches F.
    assert over("C", session, rows).session == "F"
    # Another session of fair has its own session cap, untouched.
    assert over("G", session, rows) is None
    # A window sums every session of fair and their descendants: F, W, C.
    window = {"fair": (Cap("usd", 2.5, "7d"),)}
    assert over("G", window, rows) is None
    rows.append(Row("G", NOW - 1, 0.5, 0))
    o = over("G", window, rows)
    assert o.agent == "fair" and o.spent == pytest.approx(2.6) and o.session is None
    # The worker's own agent carries no cap; opus's spend counts for no one.
    assert over("O", window, rows) is None


def test_a_24h_window_frees_when_its_oldest_spend_ages_out():
    cap = {"fair": (Cap("tokens", 1000, "24h"),)}
    rows = [Row("F", NOW - 20 * H, 0, 600), Row("F", NOW - 2 * H, 0, 300),
            Row("W", NOW - H, 0, 200)]  # fmt: skip
    o = over("F", cap, rows)
    assert o.spent == 1100
    # Without its oldest row, 500 is left: under the cap.
    assert o.frees_at == NOW - 20 * H + 24 * H
    assert over("F", cap, rows, now=o.frees_at) is None
    assert over("F", cap, rows, now=o.frees_at - 1) is not None


def test_the_cap_that_frees_last_names_the_gate():
    caps = {
        "fair": (Cap("usd", 1, "24h"), Cap("usd", 1, "7d"), Cap("usd", 3, "session"))
    }
    rows = [Row("F", NOW - 30 * H, 1.0, 0), Row("F", NOW - H, 1.0, 0)]
    o = over("F", caps, rows)
    assert o.cap.per == "7d" and o.frees_at == NOW - H + 7 * 24 * H
    rows.append(Row("F", NOW, 1.0, 0))
    assert over("F", caps, rows).cap.per == "session"


def test_the_notice_says_what_was_spent_and_until_when():
    o = budgets.Over("fair", Cap("usd", 25, "7d"), 25.4, None, NOW)
    g = budgets.gate(o, "F", str)
    assert g.state == "closed" and budgets.is_budget(g)
    assert g.why == "agent fair has spent $25.40 of $25 in the last 7d"
    text = budgets.notice(g)
    assert text.startswith(
        "agent fair has spent $25.40 of $25 in the last 7d. Finish what you are"
        " doing as soon as possible and end your turn. aegis starts no turn here"
        " that a person did not start until it has room again, at "
    )
    s = budgets.Over("fair", Cap("tokens", 1000, "session"), 1200, "F", None)
    assert budgets.gate(s, "F", str).why == (
        "agent fair has spent 1,200 of 1,000 tokens in this session"
    )
    assert budgets.gate(s, "W", lambda lid: "fair-1").why.endswith("in session fair-1")
    assert "a session cap never frees" in budgets.notice(budgets.gate(s, "F", str))


def test_spend_is_read_from_the_usage_rows(tmp_path):
    from decimal import Decimal

    from aegis.journal import db
    from aegis.usage import tables
    from aegis.usage.turns import Derived, TurnRow

    path = tmp_path / "journal.db"
    assert budgets.read(path, 0) == []  # no file yet
    con, _ = db.connect(path)
    tables.ensure(con)
    for rec, ts in ((1, 10.0), (2, 20.0)):
        t = TurnRow(rec, ts, "", "worker", "claude-code", "sonnet", "queue", "api",
                    Decimal("0.25"), "billed", 100, 50, 7, 9, 1.0, 0, 0, 0, 0, 0, None,
                    agent="deepseek", queue="fair-work", starter="F")  # fmt: skip
        tables.insert(con, "W", Derived(turns=[t]))
    con.close()
    assert budgets.read(path, 10.0) == [Row("W", 20.0, 0.25, 150, "deepseek")]
