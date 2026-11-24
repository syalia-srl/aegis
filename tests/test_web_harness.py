"""Web agents participate in normal discovery, sessions and transcript folding."""

import pytest

from aegis.harness import harness_for
from aegis.meta import MetaStore
from aegis.session import Host, Session, SpawnSpec
from aegis.transcript.entries import fold_records
from aegis.transcript.store import Store, read_store

from .conftest import until
from .test_web_process import Browser

NAMES = ("web-gemini", "web-deepseek", "web-qwen", "web-chatgpt")


@pytest.mark.parametrize("name", NAMES)
async def test_web_harnesses_have_a_catalog_without_spawning_provider_binaries(
    tmp_path, name
):
    harness = harness_for(name, "no-claude", "no-opencode")
    spec = SpawnSpec("research", "web", "", "full", tmp_path, harness=name)
    catalog = await harness.probe(spec, tmp_path / "unused.log")
    assert harness.src == "web"
    assert catalog.model("web") is not None
    assert catalog.model("web").efforts == ()
    assert not (tmp_path / "unused.log").exists()


async def test_discovery_lists_the_four_builtin_web_drivers_without_path_dependencies(
    tmp_path,
):
    from aegis.doctor import detect

    found = await detect(tmp_path, {}, only=NAMES)
    assert [f.harness for f in found] == list(NAMES)
    assert all(f.error is None and len(f.models) == 1 for f in found)
    assert all(f.models[0].value == "web" for f in found)
    assert all(f.wire()["builtin"] is True and f.wire()["bin"] is None for f in found)


async def test_web_harness_versions_do_not_probe_a_provider_cli():
    from aegis.harness_versions import check

    verdict = await check(harness_for("web-qwen", "no-claude", "no-opencode"), {})
    assert verdict.outside is False
    assert verdict.path is None


def test_a_configured_web_agent_is_enabled_and_not_required_to_name_an_api_model(
    tmp_path,
):
    from aegis.agents import agents_from

    agents = agents_from(
        {
            "agents": {
                "research": {
                    "harness": "web-qwen",
                    "model": "web",
                    "effort": "high",
                    "permission": "full",
                }
            }
        }
    )
    assert agents[0].enabled is True
    assert agents[0].error is None


class WebHost(Host):
    def __init__(self):
        self.browser = Browser(
            "printf 'native-session\\n' > native-result.txt; cat native-result.txt",
            final="Native web session complete",
        )

    def web_bridge(self, session):
        return self.browser


async def test_a_real_session_uses_the_web_driver_and_refolds_to_native_entries(
    tmp_path,
):
    host = WebHost()
    path = tmp_path / "state" / "transcripts" / "web-test.jsonl"
    session = Session(
        log_id="web-test",
        handle="research-sidekick",
        spec=SpawnSpec("research", "web", "", "full", tmp_path, harness="web-qwen"),
        store=Store(path),
        stderr_path=tmp_path / "state" / "stderr" / "web-test.log",
        claude_bin="no-claude",
        publish=lambda *_: None,
        metas=MetaStore(tmp_path / "state" / "sessions"),
        host=host,
    )
    try:
        await session.send("Check the real session integration")
        await until(
            lambda: (
                session.status == "idle"
                and any(
                    e["kind"] == "prose" and "Native web session complete" in e["md"]
                    for e in session.entries()
                )
            ),
            timeout=5,
        )
        assert (tmp_path / "native-result.txt").read_text() == "native-session\n"
        tools = [e for e in session.entries() if e["kind"] == "tool"]
        assert (
            len(tools) == 1
            and tools[0]["title"] == "Bash"
            and tools[0]["status"] == "ok"
        )
        records, damaged = read_store(path)
        assert damaged == 0
        assert fold_records(records).entries() == session.entries()
        assert session.resume_id.startswith("web-qwen:")
        assert session.cost_usd is None
    finally:
        await session.stop()


def test_connection_changes_update_one_native_transcript_row():
    records = [
        {
            "i": 0,
            "ts": 1000,
            "src": "aegis",
            "kind": "web_connection",
            "state": "waiting",
            "text": "Waiting for Chrome",
            "url": "",
            "tab": None,
        },
        {
            "i": 1,
            "ts": 1001,
            "src": "aegis",
            "kind": "web_connection",
            "state": "connected",
            "text": "Qwen web · Chrome connected",
            "url": "https://chat.qwen.ai/c/test",
            "tab": "qwen",
        },
    ]
    entries = fold_records(records).entries()
    assert len(entries) == 1
    assert entries[0]["kind"] == "web_connection"
    assert entries[0]["summary"] == "Qwen web · Chrome connected"
    assert entries[0]["detail"]["tab"] == "qwen"


def test_manual_source_messages_preserve_pending_composer_input():
    import json

    records = [
        {
            "i": 0,
            "ts": 1000,
            "src": "aegis",
            "kind": "send",
            "text": "Use the proposal",
        },
        {
            "i": 1,
            "ts": 1001,
            "src": "web",
            "line": json.dumps(
                {"type": "manual", "id": "chrome-user", "text": "Make a proposal"}
            ),
        },
        {
            "i": 2,
            "ts": 1002,
            "src": "web",
            "line": json.dumps({"type": "echo", "text": "Use the proposal"}),
        },
    ]
    entries = fold_records(records).entries()
    users = [e for e in entries if e["kind"] == "user"]
    assert len(users) == 2
    assert users[0]["md"] == "From Chrome:\n\nMake a proposal"
    assert users[0]["detail"]["message_id"] == "chrome-user"
    assert users[1]["md"] == "Use the proposal"
