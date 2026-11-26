"""The workplace primer follows the session's scope (#1066)."""

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.mcp import primer
from aegis.scopes import ANY, GROUPS, TOOLS, Scope

# SHA-256 of the unscoped primer for this session, without and with the
# browsers paragraph. The baseline includes the current Send audio gesture.
BASELINE = {
    False: "baf57bc7e6a9f3bd0c92f3dc5a5010a38d705905f71018a7d55e21f98155b180",
    True: "304ffe0b3e9445e66d5ba28b2faf8ae3a5c45ca94ab197c55ca4d330672981b9",
}


def text(scope: Scope, browsers: bool = True) -> str:
    s = SimpleNamespace(
        handle="calm-turing-1",
        harness=SimpleNamespace(tool_prefix="mcp__aegis__"),
        spec=SimpleNamespace(scope=scope),
    )
    return primer(s, "vps", Path("/w"), browsers=browsers)


@pytest.mark.parametrize("browsers", [False, True])
def test_the_unscoped_primer_matches_its_baseline(browsers):
    got = hashlib.sha256(
        text(ANY, browsers).split("\n\nDelegation discovery:")[0].encode()
    ).hexdigest()
    assert got == BASELINE[browsers]


def test_a_primer_without_workflows_never_names_them():
    t = text(Scope(TOOLS - GROUPS["workflows"]))
    assert "workflow" not in t.lower()
    assert "queue_enqueue" in t and "\n\n\n" not in t


def test_a_narrow_primer_keeps_only_the_parts_about_its_tools():
    t = text(Scope(frozenset({"turn_end", "plan_update", "toast"})))
    for tool in ("monitor_start", "feed_start", "session_spawn", "queue_enqueue"):
        assert tool not in t
    for tool in ("artifact_create", "browser_open", "secret_send", "help_search"):
        assert tool not in t
    assert "turn_end" in t and "plan_update" in t and "toast" in t
    assert "a live background task, which will" in t
    assert " \n" not in t and "\n\n\n" not in t


def test_discovery_guidance_only_names_available_delegation_tools():
    assert "Read queues_list" in text(ANY)
    assert "Read agents_list" in text(ANY)
    narrow = text(Scope(frozenset({"turn_end", "queue_enqueue"})))
    assert "queues_list" not in narrow and "agents_list" not in narrow
