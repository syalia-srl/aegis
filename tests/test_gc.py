import json
import time
from pathlib import Path

from typer.testing import CliRunner

from aegis.cli import app
from aegis.gc import run
from aegis.transcript.store import Store, packed, read_store
from aegis.usage.store import lines, sessions

DAY = 86400
NOW = 2_000_000_000.0
IMAGE = "A" * 5000


def _session(state: Path, log_id: str, *, archived: bool, age_days: float, rid: str):
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / "sessions" / f"{log_id}.json").write_text(
        json.dumps(
            {
                "log_id": log_id,
                "handle": log_id,
                "harness": "claude-code",
                "archived": archived,
                "resume_id": rid,
                "last_activity": NOW - age_days * DAY,
            }
        )
    )
    store = Store(state / "transcripts" / f"{log_id}.jsonl")
    store.append({"ts": 1.0, "src": "aegis", "kind": "spawn", "cwd": "/w"})
    shot = {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "data": IMAGE},
                        }
                    ],
                }
            ]
        },
    }
    store.append({"ts": 2.0, "src": "claude", "line": json.dumps(shot)})
    store.close()
    (state / "inbox" / log_id).mkdir(parents=True)
    (state / "inbox" / log_id / "voice.m4a").write_bytes(b"x" * 1000)


def _world(tmp_path: Path):
    state, claude = tmp_path / "state", tmp_path / "claude"
    _session(state, "old", archived=True, age_days=30, rid="rid-old")
    _session(state, "open", archived=False, age_days=30, rid="rid-open")
    _session(state, "fresh", archived=True, age_days=1, rid="rid-fresh")
    for rid in ("rid-old", "rid-open", "rid-fresh"):
        p = claude / "projects" / "-w" / f"{rid}.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n")
    (claude / "projects" / "-w" / "rid-old").mkdir()
    for fid, owner in (("f-old-xxxxxxxxxxxx", "old"), ("f-open-xxxxxxxxxxx", "open")):
        (state / "files" / fid).mkdir(parents=True)
        (state / "files" / fid / "a.mp4").write_bytes(b"v" * 2000)
        (state / "file-sessions").mkdir(exist_ok=True)
        (state / "file-sessions" / fid).write_text(owner)
    return state, claude


def test_a_dry_run_lists_what_would_go_and_deletes_nothing(tmp_path: Path):
    state, claude = _world(tmp_path)
    before = sorted(p for p in tmp_path.rglob("*"))
    report = run(state, apply=False, claude_home=claude, now=NOW)
    assert sorted(p for p in tmp_path.rglob("*")) == before
    assert report.kinds["transcripts"][0] == 1
    assert report.kinds["inbox"][0] == 1
    assert "dry run" in report.lines()[-1]


def test_an_old_archived_session_keeps_its_history_and_loses_the_rest(
    tmp_path: Path,
):
    state, claude = _world(tmp_path)
    run(state, apply=True, claude_home=claude, now=NOW)
    plain = state / "transcripts" / "old.jsonl"
    assert not plain.exists() and packed(plain).exists()
    records, damaged = read_store(plain)
    assert damaged == 0 and [r["i"] for r in records] == [0, 1]
    shot = json.loads(records[1]["line"])
    source = shot["message"]["content"][0]["content"][0]["source"]
    assert source == {"type": "base64", "data": "", "dropped": len(IMAGE)}
    assert not (state / "inbox" / "old").exists()
    assert not (state / "files" / "f-old-xxxxxxxxxxxx").exists()
    assert not (claude / "projects" / "-w" / "rid-old.jsonl").exists()
    assert not (claude / "projects" / "-w" / "rid-old").exists()


def test_open_and_recent_sessions_are_untouched(tmp_path: Path):
    state, claude = _world(tmp_path)
    run(state, apply=True, claude_home=claude, now=NOW)
    for log_id in ("open", "fresh"):
        assert (state / "transcripts" / f"{log_id}.jsonl").exists()
        assert (state / "inbox" / log_id / "voice.m4a").exists()
    assert (state / "files" / "f-open-xxxxxxxxxxx" / "a.mp4").exists()
    assert (claude / "projects" / "-w" / "rid-open.jsonl").exists()
    assert (claude / "projects" / "-w" / "rid-fresh.jsonl").exists()


def test_the_usage_readers_see_a_packed_store(tmp_path: Path):
    state, claude = _world(tmp_path)
    run(state, apply=True, claude_home=claude, now=NOW)
    found = {s.log_id: s for s in sessions(state)}
    assert set(found) == {"old", "open", "fresh"}
    assert len(list(lines(found["old"].path))) == 2


def test_appending_to_a_packed_store_unpacks_it_and_continues_numbering(
    tmp_path: Path,
):
    state, claude = _world(tmp_path)
    run(state, apply=True, claude_home=claude, now=NOW)
    plain = state / "transcripts" / "old.jsonl"
    stored = Store(plain).append({"ts": 3.0, "src": "aegis", "kind": "send"})
    assert stored["i"] == 2
    assert plain.exists() and not packed(plain).exists()
    assert [r["i"] for r in read_store(plain)[0]] == [0, 1, 2]


def test_aegis_gc_prints_what_it_freed(tmp_path: Path):
    state, claude = _world(tmp_path)
    for p in (state / "sessions").glob("*.json"):
        m = json.loads(p.read_text())
        m["last_activity"] = time.time() - (30 if p.stem == "old" else 1) * DAY
        p.write_text(json.dumps(m))
    (tmp_path / ".aegis.yaml").write_text("{}\n")
    (tmp_path / ".aegis").mkdir()
    state.rename(tmp_path / ".aegis" / "state")
    r = CliRunner().invoke(
        app,
        ["gc", "--root", str(tmp_path), "--apply"],
        env={"CLAUDE_CONFIG_DIR": str(claude)},
    )
    assert r.exit_code == 0, r.output
    assert "transcripts" in r.output and "freed" in r.output
    assert packed(tmp_path / ".aegis" / "state" / "transcripts" / "old.jsonl").exists()
    assert not (claude / "projects" / "-w" / "rid-old.jsonl").exists()
