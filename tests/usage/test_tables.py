"""The usage tables: created beside the journal's, idempotent, settled."""

from decimal import Decimal

from aegis.journal import db
from aegis.usage import tables
from aegis.usage.turns import Derived, PartRow, Settle, TurnRow, WaitRow

COLUMNS = {
    "usage_turns": "log_id rec ts person kind harness model mode billing cost cost_kind"
    " tok_in tok_out tok_cw tok_cr secs calls errors exits ctx_start ctx_peak continues"
    " agent queue starter",
    "usage_parts": "log_id rec n ts repo purpose activity settled cost tokens secs"
    " calls errors exits person kind harness model mode billing cost_kind"
    " turn agent queue",
    "usage_waits": "log_id rec ts woke_by secs",
    "usage_ctx": "log_id rec band calls cost tok_cr",
    "usage_whatif": "log_id rec at handoffs saved rewrite",
    "usage_quota": "billing window ts percent resets_at",
    "repo_owners": "repo owner remote checked_at",
    "usage_meta": "key value",
}


def _derived() -> Derived:
    turn = TurnRow(4, 1000.0, "ana", "foreground", "claude-code", "claude-opus-5-5",
                   "person", "api", Decimal("0.3"), "billed", 1, 2, 3, 4, 5.0, 2, 0, 0,
                   10, 20, None)  # fmt: skip
    parts = [
        PartRow(
            4, 0, 1000.0, "/w/atlas", "build", "editing", False, Decimal("0.2"), 6, 3.0
        ),
        PartRow(
            4, 1, 1000.0, "/w/atlas", "chat", "reading", True, Decimal("0.1"), 4, 2.0
        ),
    ]
    return Derived(turns=[turn], parts=parts, waits=[WaitRow(2, 990.0, "person", 30.0)])


def test_ensure_creates_every_table_with_its_columns(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    assert tables.ensure(con) is True
    assert tables.ensure(con) is False
    for name, cols in COLUMNS.items():
        got = [r[1] for r in con.execute(f"PRAGMA table_info({name})")]
        assert got == cols.split(), name


def test_an_insert_is_idempotent_and_a_settle_fixes_the_purpose(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    assert tables.insert(con, "s1", _derived()) == 4
    assert tables.insert(con, "s1", _derived()) == 0
    tables.insert(con, "s1", Derived(settles=[Settle(4, 0, "fix")]))
    assert con.execute(
        "SELECT purpose, settled, cost FROM usage_parts WHERE rec = 4 AND n = 0"
    ).fetchone() == ("fix", 1, 0.2)
    # A repeat of the original rows does not unsettle it.
    tables.insert(con, "s1", _derived())
    assert con.execute(
        "SELECT purpose, settled FROM usage_parts WHERE n = 0"
    ).fetchone() == ("fix", 1)


def test_a_version_bump_recreates_only_the_usage_tables(tmp_path, monkeypatch):
    con, _ = db.connect(tmp_path / "journal.db")
    db.insert(
        con, db.Entry("s1", 1, 0, 1.0, "h", None, "turn", "", "x", None, False, [])
    )
    tables.ensure(con)
    tables.insert(con, "s1", _derived())
    monkeypatch.setattr(tables, "USAGE_VERSION", tables.USAGE_VERSION + 1)
    assert tables.ensure(con) is True
    assert con.execute("SELECT count(*) FROM usage_turns").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM entries").fetchone()[0] == 1


def test_the_backfill_mark_completes_only_for_its_own_token(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    assert not tables.is_complete(con)
    tables.mark(con, 7)
    tables.clear(con, 9)  # a rebuild began meanwhile
    tables.complete_if(con, 7)
    assert not tables.is_complete(con)
    tables.complete_if(con, 9)
    assert tables.is_complete(con)


def _windows(con, *rows):
    con.executemany(
        "INSERT INTO usage_quota VALUES('claude:a', 'session', ?, ?, ?)", rows
    )


def test_usual_spend_averages_the_last_finished_windows(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    # Jitter of a second and a last row above the first: one window that ended on 30.
    _windows(
        con,
        (10.0, 5.0, 1000.0),
        (11.0, 30.0, 1001.0),
        (20.0, 50.0, 2000.0),
        (30.0, 150.0, 3000.0),  # a vendor overshoot counts as the limit
        (40.0, 90.0, 4000.0),  # still running
    )
    span = 1000.0
    assert tables.usual_spend(con, "claude:a", "session", span, 3500.0, 20) == 60.0
    assert tables.usual_spend(con, "claude:a", "session", span, 3500.0, 2) == 75.0


def test_usual_spend_with_no_finished_window_is_none(tmp_path):
    con, _ = db.connect(tmp_path / "journal.db")
    tables.ensure(con)
    _windows(con, (10.0, 5.0, 1000.0), (11.0, 7.0, None))
    assert tables.usual_spend(con, "claude:a", "session", 1000.0, 500.0, 20) is None
    assert tables.usual_spend(con, "claude:b", "session", 1000.0, 5000.0, 20) is None
    assert tables.usual_spend(con, "claude:a", "weekly_all", 1000.0, 5000.0, 20) is None


# The version 2 schema, as it was before #1068 added agent, queue and starter.
V2_SCHEMA = """
CREATE TABLE usage_turns(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, ts REAL NOT NULL, person TEXT NOT NULL,
  kind TEXT NOT NULL, harness TEXT NOT NULL, model TEXT NOT NULL, mode TEXT NOT NULL,
  billing TEXT NOT NULL, cost REAL NOT NULL, cost_kind TEXT NOT NULL,
  tok_in INTEGER NOT NULL, tok_out INTEGER NOT NULL, tok_cw INTEGER NOT NULL,
  tok_cr INTEGER NOT NULL, secs REAL NOT NULL, calls INTEGER NOT NULL,
  errors INTEGER NOT NULL, exits INTEGER NOT NULL, ctx_start INTEGER NOT NULL,
  ctx_peak INTEGER NOT NULL, continues TEXT, PRIMARY KEY(log_id, rec));
CREATE INDEX usage_turns_ts ON usage_turns(ts);
CREATE TABLE usage_parts(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, n INTEGER NOT NULL, ts REAL NOT NULL,
  repo TEXT, purpose TEXT NOT NULL, activity TEXT NOT NULL, settled INTEGER NOT NULL,
  cost REAL NOT NULL, tokens INTEGER NOT NULL, secs REAL NOT NULL,
  calls INTEGER NOT NULL, errors INTEGER NOT NULL, exits INTEGER NOT NULL,
  person TEXT NOT NULL, kind TEXT NOT NULL, harness TEXT NOT NULL, model TEXT NOT NULL,
  mode TEXT NOT NULL, billing TEXT NOT NULL, cost_kind TEXT NOT NULL,
  turn INTEGER NOT NULL, PRIMARY KEY(log_id, rec, n));
CREATE INDEX usage_parts_ts ON usage_parts(ts);
CREATE INDEX usage_parts_repo ON usage_parts(repo, ts);
CREATE TABLE usage_waits(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, ts REAL NOT NULL, woke_by TEXT NOT NULL,
  secs REAL NOT NULL, PRIMARY KEY(log_id, rec));
CREATE INDEX usage_waits_ts ON usage_waits(ts);
CREATE TABLE usage_ctx(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, band INTEGER NOT NULL,
  calls INTEGER NOT NULL, cost REAL NOT NULL, tok_cr INTEGER NOT NULL,
  PRIMARY KEY(log_id, rec, band));
CREATE TABLE usage_whatif(
  log_id TEXT NOT NULL, rec INTEGER NOT NULL, at INTEGER NOT NULL,
  handoffs INTEGER NOT NULL, saved REAL NOT NULL, rewrite REAL NOT NULL,
  PRIMARY KEY(log_id, rec, at));
CREATE TABLE usage_quota(
  billing TEXT NOT NULL, window TEXT NOT NULL, ts REAL NOT NULL, percent REAL NOT NULL,
  resets_at REAL, PRIMARY KEY(billing, window, ts));
CREATE TABLE repo_owners(
  repo TEXT PRIMARY KEY, owner TEXT NOT NULL, remote TEXT, checked_at REAL NOT NULL);
CREATE TABLE usage_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE usage_imports(path TEXT PRIMARY KEY, size INTEGER NOT NULL, mtime REAL NOT NULL);
CREATE TABLE usage_import_ids(mid TEXT PRIMARY KEY, path TEXT NOT NULL);
CREATE INDEX usage_import_ids_path ON usage_import_ids(path);
"""


def test_a_version_2_file_keeps_its_quota_readings_and_imports(tmp_path):
    """2 -> 3 adds columns in place: quota readings and imported sessions are
    in no store, so a drop would lose them for good."""
    con, _ = db.connect(tmp_path / "j.db")
    for stmt in V2_SCHEMA.split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.execute("INSERT INTO usage_meta VALUES('version', '2')")
    con.execute("INSERT INTO usage_meta VALUES('backfill', '7')")
    con.execute(
        "INSERT INTO usage_quota VALUES('claude', 'weekly_all', 1.0, 37.0, 9.0)"
    )
    con.execute("INSERT INTO usage_imports VALUES('/x.jsonl', 10, 1.0)")
    # A turn a store holds and one imported from Claude Code (cc-).
    for log_id in ("20261020-120000-aaaaaa", "cc-abc"):
        con.execute(
            "INSERT INTO usage_turns VALUES(?,1,1.0,'','foreground','claude-code',"
            "'opus','default','api',0.5,'billed',1,1,0,0,1.0,0,0,0,0,0,NULL)",
            (log_id,),
        )
    con.commit()
    assert tables.ensure(con)
    assert con.execute("SELECT count(*) FROM usage_quota").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM usage_imports").fetchone()[0] == 1
    # The store's row is gone, for the backfill to derive it with an agent;
    # the imported one is in no store and stays.
    assert con.execute("SELECT log_id FROM usage_turns").fetchall() == [("cc-abc",)]
    indexes = {r[1] for r in con.execute("PRAGMA index_list(usage_turns)")}
    assert {"usage_turns_agent", "usage_turns_starter"} <= indexes
    for t, cols in (
        ("usage_turns", COLUMNS["usage_turns"]),
        ("usage_parts", COLUMNS["usage_parts"]),
    ):
        got = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
        assert got == cols.split()
    assert not tables.is_complete(con)
    assert not tables.ensure(con)  # settled: a second start changes nothing
