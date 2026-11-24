"""Execution requests must not turn research examples or replays into shell work."""

import importlib

import pytest


def protocol():
    return importlib.import_module("aegis.web_agent.protocol")


def test_only_the_marked_top_level_block_is_executable():
    got = protocol().request(
        "I will inspect the files.\n\n```bash\n# aegis: execute\n"
        "# Count files\nprintf '3\\n'\n```\n\nThen compare the options.\n"
    )
    assert got.code == "# aegis: execute\n# Count files\nprintf '3\\n'\n"
    assert "I will inspect the files." in got.prose
    assert "Then compare the options." in got.prose
    assert "printf" not in got.prose


@pytest.mark.parametrize(
    "text",
    [
        "```bash\necho example\n```",
        "```python\n# aegis: execute\nprint(3)\n```",
        "```bash\n# aegis: execute\necho unfinished",
        "> ```bash\n> # aegis: execute\n> echo quoted\n> ```",
        "- Example:\n\n  ```bash\n  # aegis: execute\n  echo listed\n  ```",
        "```bash\n# other comment\n# aegis: execute\necho example\n```",
        "```bash extra\n# aegis: execute\necho example\n```",
        "There are no commands here.",
    ],
)
def test_examples_nested_blocks_and_incomplete_fences_do_not_execute(text):
    assert protocol().request(text) is None


def test_multiple_marked_blocks_are_refused_as_a_whole():
    p = protocol()
    block = "```bash\n# aegis: execute\necho three\n```\n"
    with pytest.raises(p.ProtocolError, match="one"):
        p.request(block + block)


def test_a_blank_line_before_the_marker_and_tilde_fences_are_supported():
    got = protocol().request("~~~~bash\n\n# aegis: execute\necho 3\n~~~~")
    assert got.code == "\n# aegis: execute\necho 3\n"


def ledger_type():
    return importlib.import_module("aegis.web_agent.ledger").Ledger


def test_an_execution_started_before_a_crash_is_uncertain(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ledger_type()(path)
    ledger.prepare("turn-1/0", "echo first")
    ledger.start("turn-1/0")
    resumed = ledger_type()(path)
    assert resumed.uncertain() == ["turn-1/0"]
    assert resumed.get("turn-1/0")["phase"] == "started"


def test_results_and_delivery_survive_restarting_the_bridge(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ledger_type()(path)
    ledger.prepare("turn-1/0", "echo first")
    ledger.start("turn-1/0")
    ledger.complete("turn-1/0", {"stdout": "first\n", "stderr": "", "code": 0})
    ledger.delivery_pending("turn-1/0")
    resumed = ledger_type()(path)
    assert resumed.get("turn-1/0")["result"]["stdout"] == "first\n"
    assert resumed.pending() == ["turn-1/0"]
    resumed.delivered("turn-1/0")
    assert ledger_type()(path).pending() == []


def test_repeated_command_content_does_not_collapse_distinct_turns(tmp_path):
    ledger = ledger_type()(tmp_path / "ledger.json")
    ledger.prepare("turn-1/0", "echo same")
    ledger.prepare("turn-2/0", "echo same")
    assert ledger.get("turn-1/0") is not None
    assert ledger.get("turn-2/0") is not None


def test_the_same_message_cannot_change_its_command_after_preparation(tmp_path):
    ledger = ledger_type()(tmp_path / "ledger.json")
    ledger.prepare("turn-1/0", "echo first")
    with pytest.raises(ValueError, match="changed"):
        ledger.prepare("turn-1/0", "echo changed")


def test_an_existing_execution_is_not_reset_by_an_observation_replay(tmp_path):
    ledger = ledger_type()(tmp_path / "ledger.json")
    ledger.prepare("turn-1/0", "echo first")
    ledger.start("turn-1/0")
    record = ledger.prepare("turn-1/0", "echo first")
    assert record["phase"] == "started"


def test_saved_conversation_and_priming_are_reused_after_restart(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = ledger_type()(path)
    ledger.update(conversation={"url": "https://chat.qwen.ai/c/test", "device": "d"}, primed=True)
    resumed = ledger_type()(path)
    assert resumed.data["primed"] is True
    assert resumed.data["conversation"]["url"] == "https://chat.qwen.ai/c/test"
    assert path.stat().st_mode & 0o777 == 0o600
