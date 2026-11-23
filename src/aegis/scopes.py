"""Agent scopes: which aegis tools, queues and agents a session may use (#1066).

An agent preset's ``tools:`` block names them: ``allow`` or ``deny`` over tool
names and the groups below, and ``queues:`` and ``agents:`` lists that narrow
the tools taking a queue or an agent. A preset without the block has the scope
that allows everything, as before scopes.

A session's scope is fixed at spawn and recorded in its spawn record, like its
priming: editing the agent never widens a running session. It is its own
agent's scope met with its starter's (``Scope.meet``), so an agent allowed one
queue cannot escape through a worker whose agent is wider.

``TOOLS`` lists aegis's agent tools by name. It is a fixed list rather than
the registry's, because the file is read before any registry exists;
tests/test_scopes.py fails when the two differ.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

TOOLS = frozenset(
    """
    agents_list annotation_view artifact_close artifact_create artifact_read
    artifact_send artifact_update browser_click browser_close browser_eval
    browser_events
    browser_goto browser_handover browser_list browser_open browser_press
    browser_request browser_resize browser_screenshot browser_snapshot
    browser_type config_doctor config_suggest feed_cancel feed_list feed_pause
    feed_resume feed_start file_send help_read help_search journal_note
    journal_search knowledge_import knowledge_stats layout_read layout_set
    memory_delete memory_read memory_save memory_search mention_reply meta
    models_list
    monitor_cancel monitor_list monitor_sessions monitor_start notice_announce
    peer_handoff peer_read plan_update queue_enqueue quota_read schedule_create
    schedule_delete schedule_fire schedule_list schedule_pause schedule_resume
    schedule_runs schedule_test schedule_update secret_request secret_send
    session_close session_group session_handoff session_list session_quiet
    session_rename session_spawn setup_record signin_open skill_delete
    skill_read skill_run skill_save skill_search skill_tool table_send
    task_cancel task_resume task_status toast turn_end usage_import usage_read
    workflow_answer workflow_arm workflow_ask workflow_delete workflow_edit
    workflow_lint workflow_list workflow_note workflow_output workflow_result
    workflow_save workflow_status workflow_stop
    """.split()
)


def _prefixed(prefix: str) -> frozenset[str]:
    return frozenset(t for t in TOOLS if t.startswith(prefix))


GROUPS: dict[str, frozenset[str]] = {
    "spawn": frozenset(
        {"session_spawn", "session_handoff", "peer_handoff", "session_close"}
    ),
    "queues": frozenset({"queue_enqueue", "task_status", "task_cancel", "task_resume"}),
    "workflows": _prefixed("workflow_"),
    "schedules": _prefixed("schedule_"),
    "browser": _prefixed("browser_") | {"signin_open"},
    "secrets": frozenset({"secret_send", "secret_request"}),
    "config": frozenset(
        {
            "config_suggest",
            "layout_set",
            "skill_save",
            "skill_delete",
            "notice_announce",
        }
    ),
    "help": frozenset({"help_search", "help_read", "meta"}),
}

# Kept in every scope: a session without it cannot hand back its turn.
ALWAYS = frozenset({"turn_end"})

KEYS = ("allow", "deny", "queues", "agents")


def _meet(a: frozenset[str] | None, b: frozenset[str] | None) -> frozenset[str] | None:
    if a is None:
        return b
    if b is None:
        return a
    return a & b


@dataclass(frozen=True)
class Scope:
    """What a session may use; None in a field is any."""

    tools: frozenset[str] | None = None
    queues: frozenset[str] | None = None
    agents: frozenset[str] | None = None

    @property
    def any(self) -> bool:
        return self.tools is None and self.queues is None and self.agents is None

    def meet(self, other: Scope | None) -> Scope:
        """What both allow: the intersection of each field, None the identity."""
        if other is None:
            return self
        return Scope(
            _meet(self.tools, other.tools),
            _meet(self.queues, other.queues),
            _meet(self.agents, other.agents),
        )

    def record(self) -> dict:
        """The fields a spawn record carries: those that are not any."""
        return {
            k: sorted(v)
            for k in ("tools", "queues", "agents")
            if (v := getattr(self, k)) is not None
        }

    @classmethod
    def from_record(cls, d: Any) -> Scope:
        d = d if isinstance(d, dict) else {}

        def names(k: str) -> frozenset[str] | None:
            v = d.get(k)
            return None if v is None else frozenset(str(n) for n in v)

        return cls(names("tools"), names("queues"), names("agents"))


ANY = Scope()


def _names(raw: Any, key: str) -> tuple[list[str], str | None]:
    if not isinstance(raw, list) or not all(isinstance(n, str) and n for n in raw):
        return [], f"tools.{key} must be a list of names"
    return [str(n) for n in raw], None


def _expand(names: list[str], key: str) -> tuple[frozenset[str], str | None]:
    unknown = [n for n in names if n not in GROUPS and n not in TOOLS]
    if unknown:
        return frozenset(), (
            f"tools.{key} names {', '.join(unknown)}, which is neither an aegis "
            f"tool nor a group; the groups are {', '.join(GROUPS)}"
        )
    out: set[str] = set()
    for n in names:
        out |= GROUPS.get(n, {n})
    return frozenset(out), None


def parse(raw: Any) -> tuple[Scope, str | None]:
    """An agent's ``tools:`` block as a scope, or the error that stops the
    agent. A name aegis does not know is refused rather than ignored: a typo
    in a deny list would otherwise leave the tool open."""
    if raw is None:
        return ANY, None
    if not isinstance(raw, dict):
        return ANY, "tools must be a map of allow or deny, queues and agents"
    if extra := [str(k) for k in raw if k not in KEYS]:
        return (
            ANY,
            f"tools does not take {', '.join(extra)}; it takes {', '.join(KEYS)}",
        )
    if "allow" in raw and "deny" in raw:
        return ANY, "tools takes allow or deny, not both"
    tools = None
    for key in ("allow", "deny"):
        if key not in raw:
            continue
        names, error = _names(raw[key], key)
        named, error = (frozenset(), error) if error else _expand(names, key)
        if error:
            return ANY, error
        tools = (named if key == "allow" else TOOLS - named) | ALWAYS
    lists: dict[str, frozenset[str] | None] = {}
    for key in ("queues", "agents"):
        if key not in raw:
            lists[key] = None
            continue
        names, error = _names(raw[key], key)
        if error:
            return ANY, error
        lists[key] = frozenset(names)
    return Scope(tools, lists["queues"], lists["agents"]), None


def form(raw: Any) -> dict:
    """The ``tools:`` block as the file writes it, for the agent's wire form;
    {} when it has none or one parse refuses."""
    if parse(raw)[1] is not None or not raw:
        return {}
    return {k: list(raw[k]) for k in KEYS if k in raw}


def describe(raw: Any) -> str:
    """One line for the agent's row in ``aegis doctor``; "" without a block."""
    f = form(raw)
    parts = [f"{k} {', '.join(f[k]) or 'none'}" for k in KEYS if k in f]
    return "; ".join(parts)


def _allowed(names: frozenset[str]) -> str:
    return ", ".join(sorted(names)) or "none"


def check_tool(scope: Scope, tool: str) -> None:
    """Refuse a call to a tool outside ``scope`` with out_of_scope."""
    if scope.tools is None or tool in scope.tools:
        return
    from .ops import OpError

    shut = TOOLS - scope.tools
    said = (
        f"it may call {_allowed(scope.tools)}"
        if len(scope.tools) <= len(shut)
        else f"it may call every aegis tool but {_allowed(shut)}"
    )
    raise OpError("out_of_scope", f"{tool} is outside this session's scope; {said}")


def check_name(scope: Scope, kind: str, name: str) -> None:
    """Refuse a queue or an agent outside ``scope`` with out_of_scope; ``kind``
    is "queues" or "agents"."""
    allowed = getattr(scope, kind)
    if allowed is None or name in allowed:
        return
    from .ops import OpError

    what = "queue" if kind == "queues" else "agent"
    raise OpError(
        "out_of_scope",
        f"{what} {name} is outside this session's scope; its {kind}: "
        f"{_allowed(allowed)}",
    )
