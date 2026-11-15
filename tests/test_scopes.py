"""Agent scopes (#1066): the tools: block, and a session's scope."""

from pathlib import Path

import pytest

from aegis import scopes
from aegis.agents import load_agents
from aegis.doctor import _agent as doctor_row
from aegis.scopes import ANY, GROUPS, TOOLS, Scope

from .test_agents import CONFIG, World

PRESET = "{harness: claude-code, model: opus, effort: high, permission: full"


def agent(tmp_path: Path, tools: str):
    (tmp_path / ".aegis.yaml").write_text(f"agents:\n  a: {PRESET}, tools: {tools}}}\n")
    (a,) = load_agents(tmp_path)
    return a


def test_a_preset_without_tools_has_the_scope_that_allows_everything(tmp_path):
    (tmp_path / ".aegis.yaml").write_text(f"agents:\n  a: {PRESET}}}\n")
    (a,) = load_agents(tmp_path)
    assert a.scope == ANY and a.scope.any and a.error is None
    assert "tools" not in a.as_dict()


def test_deny_expands_its_groups_and_keeps_the_rest(tmp_path):
    a = agent(tmp_path, "{deny: [spawn, workflows], queues: [fair-work], agents: []}")
    assert a.error is None
    assert a.scope.tools == TOOLS - GROUPS["spawn"] - GROUPS["workflows"]
    assert "workflow_arm" not in a.scope.tools and "plan_update" in a.scope.tools
    assert a.scope.queues == {"fair-work"} and a.scope.agents == frozenset()
    assert a.as_dict()["tools"] == {
        "deny": ["spawn", "workflows"],
        "queues": ["fair-work"],
        "agents": [],
    }


def test_allow_names_tools_and_groups_and_always_keeps_turn_end(tmp_path):
    a = agent(tmp_path, "{allow: [plan_update, help]}")
    assert a.scope.tools == {"plan_update", "turn_end"} | GROUPS["help"]
    assert a.scope.queues is None and a.scope.agents is None
    assert "turn_end" in agent(tmp_path, "{deny: [turn_end]}").scope.tools


@pytest.mark.parametrize(
    ("tools", "says"),
    [
        ("{allow: [toast], deny: [spawn]}", "allow or deny, not both"),
        ("{deny: [workflow]}", "workflow, which is neither"),
        ("{allow: [plan_updat]}", "plan_updat"),
        ("{queues: fair}", "tools.queues must be a list"),
        ("{only: [toast]}", "does not take only"),
        ("[toast]", "tools must be a map"),
    ],
)
def test_a_bad_block_stops_the_agent(tmp_path, tools, says):
    a = agent(tmp_path, tools)
    assert not a.enabled and says in (a.error or "")


def test_meet_intersects_and_any_is_its_identity():
    a = Scope(frozenset({"x", "y", "turn_end"}), frozenset({"q"}), None)
    b = Scope(None, frozenset({"q", "r"}), frozenset())
    assert a.meet(b) == Scope(
        frozenset({"x", "y", "turn_end"}), frozenset({"q"}), frozenset()
    )
    assert a.meet(ANY) == a == ANY.meet(a) and a.meet(None) == a
    assert Scope.from_record(a.meet(b).record()) == a.meet(b)
    assert ANY.record() == {} and Scope.from_record({}) == ANY


def test_doctor_shows_the_scope_on_the_agents_row(tmp_path):
    a = agent(tmp_path, "{deny: [spawn], queues: [w]}")
    (row,) = doctor_row(a, None)
    assert row.level == "ok" and row.message.endswith("; tools: deny spawn; queues w")


async def test_the_fixed_list_names_every_agent_tool(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = World(tmp_path, fake_claude)
    await w.start()
    try:
        names = {op.tool_name for op in w.app.registry.agent_ops()}
    finally:
        await w.stop()
    assert scopes.TOOLS == names
    assert all(g <= names for g in GROUPS.values())


SCOPED = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  fair:
    harness: claude-code
    model: opus
    effort: high
    permission: full
    tools: {deny: [workflows], queues: [general]}
queues:
  general: {agent: opus}
  other: {agent: opus}
"""


@pytest.fixture
async def scoped(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(SCOPED)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


async def spawn(w, agent: str, by=None):
    from aegis.ops import Caller

    caller = Caller("agent", by.log_id) if by is not None else None
    args = ("session.spawn", {"agent": agent}) + ((caller,) if caller else ())
    r = await w.app.registry.call(*args)
    return w.app.sessions.sessions[r["log_id"]]


def test_a_spec_keeps_its_scope_through_its_record(tmp_path):
    from aegis.session import SpawnSpec

    scope = Scope(frozenset({"turn_end", "toast"}), frozenset({"q"}), None)
    spec = SpawnSpec("a", "opus", "high", "full", tmp_path, scope=scope, started_by="x")
    back = SpawnSpec.from_record(spec.record(), tmp_path)
    assert back.scope == scope and back.started_by == "x"
    plain = SpawnSpec("a", "opus", "high", "full", tmp_path)
    assert "scope" not in plain.record()
    assert SpawnSpec.from_record(plain.record(), tmp_path).scope == ANY


async def test_a_spawn_from_a_scoped_session_meets_its_scope(scoped):
    fair = await spawn(scoped, "fair")
    assert "workflow_arm" not in fair.spec.scope.tools
    child = await spawn(scoped, "opus", by=fair)
    assert child.spec.scope == fair.spec.scope
    assert (await spawn(scoped, "opus")).spec.scope == ANY


async def test_a_worker_of_a_scoped_enqueuer_has_its_scope(scoped):
    from aegis.ops import Caller

    from .conftest import until

    fair = await spawn(scoped, "fair")
    r = await scoped.app.registry.call(
        "queue.enqueue",
        {"queue": "general", "payload": "hello", "callback": False},
        Caller("agent", fair.log_id),
    )
    t = scoped.app.queues.tasks[r["task_id"]]
    await until(lambda: t.worker, timeout=10, what="the worker")
    meta = scoped.app.sessions.archived.get(t.worker)
    spec = meta if meta is not None else scoped.session(t.worker).spec.record()
    assert spec["agent"] == "opus" and spec["started_by"] == fair.log_id
    worker = Scope.from_record(spec["scope"])
    assert worker == fair.spec.scope and "workflow_arm" not in worker.tools
