"""can on every operation that names a session (#959)."""

from __future__ import annotations

import types
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from aegis.app import App
from aegis.ops import Caller, OpError, Principal
from aegis.policy import Refused
from aegis.roots import make_roots

from .policies import Named, Who

SESSIONISH = {
    "log_id",
    "target",
    "handle",
    "session",
    "task_id",
    "run",
    "browser",
    "artifact_id",
    "from_agent",
    "worker",
    "id",
    "monitor_id",
}

# Fields with those names that do not name something a session owns, and why.
NOT_AN_ID: dict[tuple[str, str], str] = {
    **{
        (op, "target"): "a CSS target in the page"
        for op in (
            "browser.click",
            "browser.press",
            "browser.screenshot",
            "browser.type",
        )
    },
    ("journal.rows", "session"): "a filter on rows; lists are task 5's",
    ("journal.search", "session"): "a filter on rows; lists are task 5's",
    **{
        (op, "from_agent"): "provenance from a linked server, never authorization"
        for op in (
            "queue.enqueue",
            "schedule.create",
            "schedule.delete",
            "schedule.fire",
            "schedule.pause",
            "schedule.resume",
            "schedule.update",
        )
    },
    **{
        (op, "id"): "an artifact of the calling agent's own session"
        for op in (
            "artifact.close",
            "artifact.read",
            "artifact.send",
            "artifact.update",
        )
    },
    **{
        (op, "id"): "a device's push subscription, the caller's own"
        for op in ("push.remove", "push.subscribe", "push.test", "push.update")
    },
    **{
        (op, "id"): "a terminal; term.* act on the whole server (Operation.server)"
        for op in ("term.close", "term.input", "term.resize", "term.restart")
    },
    **{
        (op, "id"): "a saved workflow definition, the server's"
        for op in ("workflow.arm", "workflow.delete", "workflow.read")
    },
    **{
        (op, "artifact_id"): "looked up inside the log_id's session"
        for op in (
            "artifact.emit",
            "artifact.error",
            "artifact.state",
            "artifact.submit",
            "artifact.upload",
        )
    },
}

NOT_FOUND = {
    "session": "no_session",
    "task": "no_task",
    "run": "no_run",
    "browser": "unknown_browser",
    "monitor": "no_monitor",
}

ANA_S = "20260101-000000-aaaaaa"
BEN = Caller("user", principal=Principal("ben", Who("ben")))
ANA = Caller("user", principal=Principal("ana", Who("ana")))


@pytest.fixture
def hidden(tmp_path: Path, monkeypatch) -> App:
    """ana owns ANA_S, an archived session; ben cannot see ana's sessions.
    Every task, run and browser id resolves to ANA_S."""
    app = App(make_roots(tmp_path, None), policy=Named(hidden={"ben": {"ana"}}))
    app.sessions.archived[ANA_S] = {
        "log_id": ANA_S,
        "handle": "ana-one",
        "owner": "ana",
        "agent": "opus",
        "archived": True,
    }
    real = app.session_of
    monkeypatch.setattr(
        app,
        "session_of",
        lambda kind, value: ANA_S if kind != "session" else real(kind, value),
    )
    return app


def _filler(annotation, value: str):
    origin = typing.get_origin(annotation)
    args = [a for a in typing.get_args(annotation) if a is not type(None)]
    if origin in (typing.Union, types.UnionType) and args:
        return _filler(args[0], value)
    if origin is typing.Literal:
        return typing.get_args(annotation)[0]
    if annotation is bool:
        return False
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if origin in (list, tuple) or annotation in (list, tuple):
        return []
    if origin is dict or annotation is dict:
        return {}
    return value


# Params the filler cannot build, by operation.
SENDER = {"handle": "h", "server": "far", "user": "u"}
HAND: dict[str, dict] = {
    "session.send": {"text": "x"},
    "transcript.detail": {"ids": ["e1"]},
    "peer.deliver": {"sender": SENDER},
    "peer.close": {"sender": {"handle": "h", "server": "far"}},
    "browser.resize": {"width": 800, "height": 600},
    "browser.view": {"width": 800, "height": 600},
    "knowledge.import": {"items": [{"source": "s", "kind": "memory"}]},
}


def fill(op, field: str, value: str) -> dict:
    model: type[BaseModel] = op.params
    out = dict(HAND.get(op.name, {}))
    for name, f in model.model_fields.items():
        if name == field:
            out[name] = value
        elif f.is_required() and name not in out:
            out[name] = _filler(f.annotation, "x")
    return out


def test_every_op_with_an_id_field_declares_it(hidden):
    missing = [
        (op.name, f)
        for op in hidden.registry._ops.values()
        for f in op.params.model_fields
        if f in SESSIONISH
        and f not in dict(op.names)
        and (op.name, f) not in NOT_AN_ID
        # An id looked up inside the session the call's log_id names.
        and not (f == "id" and "log_id" in dict(op.names))
    ]
    assert missing == []


async def test_a_hidden_session_answers_as_missing_from_every_op_that_names_it(hidden):
    walked, wrong = 0, []
    for op in hidden.registry._ops.values():
        if op.server:
            continue  # refused to every host caller (test_policy_review)
        for field, kind in op.names:
            value = "ana-one" if kind == "session" else "some-id"
            try:
                await hidden.registry.call(op.name, fill(op, field, value), BEN)
            except OpError as e:
                if e.code != NOT_FOUND[kind]:
                    wrong.append((op.name, field, e.code, e.message[:80]))
            else:
                wrong.append((op.name, field, "ran", ""))
            walked += 1
    assert not wrong, "\n".join(map(str, wrong))
    assert walked >= 60


async def test_a_readable_unwritable_session_refuses_writes_and_serves_reads(hidden):
    hidden.policy.hidden.clear()
    hidden.policy.read_only["ben"] = {"ana"}
    with pytest.raises(Refused):
        await hidden.registry.call("session.send", {"log_id": ANA_S, "text": "x"}, BEN)
    with pytest.raises(OpError) as e:
        await hidden.registry.call("session.read", {"log_id": ANA_S}, BEN)
    assert e.value.code != "refused"


async def test_the_owner_still_reaches_their_session(hidden):
    with pytest.raises(OpError) as e:
        await hidden.registry.call("session.send", {"log_id": ANA_S, "text": "x"}, ANA)
    assert (
        e.value.code not in ("refused", "no_session") or "archived" in e.value.message
    )


def test_session_of_resolves_handles_log_ids_and_other_servers(tmp_path):
    app = App(make_roots(tmp_path, None), server_name="here")
    app.sessions.archived[ANA_S] = {
        "log_id": ANA_S,
        "handle": "ana-one",
        "owner": "ana",
    }
    assert app.session_of("session", ANA_S) == ANA_S
    assert app.session_of("session", "ana-one") == ANA_S
    assert app.session_of("session", "ana-one@here") == ANA_S
    assert app.session_of("session", "ana-one@far") is None
    assert app.session_of("session", "nobody") is None
    app.queues.tasks["t1"] = types.SimpleNamespace(worker=None, enqueuer=ANA_S)
    assert app.session_of("task", "t1") == ANA_S
    app.workflows.runs["r1"] = types.SimpleNamespace(owner=ANA_S)
    assert app.session_of("run", "r1") == ANA_S
    app.browsers.open["b-1"] = types.SimpleNamespace(meta={"session": ANA_S})
    assert app.session_of("browser", "b-1") == ANA_S


async def test_an_agent_whose_own_session_is_hidden_from_its_person_is_refused(hidden):
    hidden.policy.hidden["ana"] = {"ana"}
    with pytest.raises(OpError) as e:
        await hidden.registry.call("session.list", {}, Caller("agent", ANA_S))
    assert e.value.code == "no_session"
