"""A workflow's recap (#1190): the run's logical tree in, plain text out, one
shared call per run while an expanded card is visible, and nothing after the
last one closes."""

import asyncio
import json

import pytest

from aegis import oneshot
from aegis.ops import Caller, OpError

from .conftest import until
from .test_agents import CONFIG, World, mcp, turn
from .test_workflows_e2e import RESULT, approve, flow, ok

NOTES = (
    "  notes: {harness: claude-code, model: recap-model, effort: low, "
    "permission: read}\n"
)
RECAPPED = CONFIG.replace("queues:", NOTES + "queues:") + "recap: {agent: notes}\n"

SLOW = f"""id: slow
title: Slow run
say: One step finishes, then a pause, then another.
root:
  seq:
    - agent: {{agent: opus, prompt: {json.dumps(RESULT)},
              result: {{type: object, properties: {{n: {{type: integer}}}}, required: [n]}}}}
      id: one
      say: Do the first thing.
    - {{sh: "sleep 2", id: pause, say: Pause.}}
    - agent: {{agent: opus, prompt: {json.dumps(RESULT)}}}
      id: two
      say: Do the last thing.
  say: Both things.
"""

GATED = """id: gated
title: Gated
say: Ask before shipping.
root:
  seq:
    - {sh: "printf 41", id: a, say: Count.}
    - {gate: {by: person, ask: "Ship it with {{a.out}}?", show: [a.out]}, id: g, say: Ask the person.}
    - {sh: "true", id: ship, say: Ship.}
  say: Three.
"""


@pytest.fixture
async def world(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(RECAPPED)
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def answer(text: str) -> str:
    return json.dumps(
        {
            "result": text,
            "structured_output": {"text": text},
            "total_cost_usd": 0.001,
            "duration_ms": 5,
        }
    )


class Model:
    """A one-shot stand-in that records each call and can hold it open."""

    def __init__(self, texts: list[str] | None = None, fail: bool = False):
        self.calls: list[dict] = []
        self.texts = texts or []
        self.fail = fail
        self.gates = [asyncio.Event() for _ in range(8)]

    async def run(self, argv, cwd, timeout, prompt, harness="claude-code"):
        i = len(self.calls)
        self.calls.append({"argv": argv, "prompt": prompt})
        if i < len(self.gates):
            await self.gates[i].wait()
        if self.fail:
            raise oneshot.OneshotError("the model is down")
        text = self.texts[i] if i < len(self.texts) else f"recap {i + 1}"
        return answer(text)

    def release(self, i: int = 0) -> None:
        self.gates[i].set()


async def armed(world, definition: str | None = None, args: dict | None = None) -> str:
    main = await world.spawn()
    said = await turn(
        main,
        mcp(
            "workflow_arm",
            definition=definition or flow(RESULT, RESULT),
            args=args or {},
        ),
    )
    return ok(said)["run"]


async def done(world, run: str) -> None:
    await approve(world, run)
    await until(
        lambda: world.app.workflows.runs[run].status == "done",
        timeout=20,
        what="the run's end",
    )


def recap(world, run: str) -> dict:
    return world.app.workflows.wire(run).get("recap") or {}


def state_of(world, run: str, node: str) -> str:
    return next(
        (r["state"] for r in world.app.workflows.wire(run)["tree"] if r["id"] == node),
        "",
    )


async def opened(world, run: str) -> dict:
    return await world.app.registry.call("workflow.recap.open", {"run": run})


async def refreshed(world, run: str, view: str) -> dict:
    return await world.app.registry.call(
        "workflow.recap.refresh", {"run": run, "view": view}
    )


async def closed(world, run: str, view: str) -> None:
    await world.app.registry.call("workflow.recap.close", {"run": run, "view": view})


async def test_the_input_is_the_tree_and_the_configured_agent_answers_it(
    world, monkeypatch
):
    real = oneshot.run
    calls: list[dict] = []

    async def spy(argv, cwd, timeout, prompt, harness="claude-code"):
        calls.append({"argv": argv, "prompt": prompt})
        return await real(argv, cwd, timeout, prompt, harness=harness)

    monkeypatch.setattr(oneshot, "run", spy)
    definition = flow(RESULT, RESULT).replace(
        "root:",
        "args: {type: object, properties: {token: {type: string}}}\nroot:",
    )
    run = await armed(world, definition, {"token": "SECRET ARG"})
    await done(world, run)
    view = await opened(world, run)
    assert view["status"] == "loading" and view["view"]
    await until(lambda: bool(recap(world, run).get("text")), timeout=15)

    assert len(calls) == 1
    argv, prompt = calls[0]["argv"], calls[0]["prompt"]
    assert argv[argv.index("--model") + 1] == "recap-model"
    for wanted in [
        "Two steps",
        "Do one thing, then another.",
        "status: done",
        "Do the first thing.",
        "outcome: Did it, n is 7.",
    ]:
        assert wanted in prompt, wanted
    for secret in ["SECRET ARG", "workflow_result", '"value"']:
        assert secret not in prompt, secret
    assert recap(world, run)["text"].startswith("The run ended done")
    assert world.app.workflow_recaps._saved[run]["model"] == "recap-model"


async def test_two_open_views_share_one_call_and_same_state_costs_none(
    world, monkeypatch
):
    model = Model()
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world)
    a = await opened(world, run)
    b = await opened(world, run)
    assert a["view"] != b["view"]
    await until(lambda: len(model.calls) == 1, timeout=5)
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1  # two views, one generation
    model.release()
    await until(lambda: recap(world, run).get("text"), timeout=5)

    # The same state again, from either view: no call.
    assert (await refreshed(world, run, a["view"]))["status"] == "current"
    assert (await refreshed(world, run, b["view"]))["status"] == "current"
    await closed(world, run, a["view"])
    assert (await refreshed(world, run, b["view"]))["status"] == "current"
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1


async def test_the_newest_state_wins_and_hiding_both_drops_the_queue(
    world, monkeypatch
):
    model = Model(texts=["old", "new", "catch-up"])
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world, SLOW)
    a = await opened(world, run)
    b = await opened(world, run)
    await until(lambda: len(model.calls) == 1, timeout=5)
    # The run leaves armed while the first answer is out: one generation for
    # that state is queued, not a second concurrent call.
    await approve(world, run)
    await until(
        lambda: world.app.workflows.runs[run].status == "done",
        timeout=20,
        what="the run's end",
    )
    await refreshed(world, run, a["view"])
    await refreshed(world, run, b["view"])
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1
    # Both views close before it finishes: the queued refresh is dropped.
    await closed(world, run, a["view"])
    await closed(world, run, b["view"])
    model.release(0)
    await until(lambda: recap(world, run).get("text") == "old", timeout=5)
    await asyncio.sleep(0.2)
    assert len(model.calls) == 1  # the dispatched call finished and cached
    assert recap(world, run).get("stale") is True
    # Reopening compares the saved input with current state: one catch-up.
    c = await opened(world, run)
    await until(lambda: len(model.calls) == 2, timeout=5)
    model.release(1)
    await until(lambda: recap(world, run).get("text") == "new", timeout=5)
    assert recap(world, run).get("stale") in (None, False)
    await closed(world, run, c["view"])


async def test_a_state_that_changed_during_a_call_gets_one_newest_answer(
    world, monkeypatch
):
    model = Model(texts=["for armed", "for done"])
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world, SLOW)
    view = await opened(world, run)
    await until(lambda: len(model.calls) == 1, timeout=5)
    await approve(world, run)
    await until(
        lambda: state_of(world, run, "one") == "done", timeout=20, what="the first step"
    )
    await until(
        lambda: world.app.workflows.runs[run].status == "done",
        timeout=20,
        what="the run's end",
    )
    await refreshed(world, run, view["view"])
    model.release(0)
    await until(lambda: len(model.calls) == 2, timeout=5)
    model.release(1)
    await until(lambda: recap(world, run).get("text") == "for done", timeout=5)
    assert "for armed" not in recap(world, run)["text"]
    assert "status: done" in model.calls[1]["prompt"]
    await closed(world, run, view["view"])


async def test_disabled_failed_blocked_and_terminal(world, monkeypatch, tmp_path):
    run = await armed(world, GATED)
    # Disabled: ordinary workflow status, no text.
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    off = await opened(world, run)
    assert off["status"] == "off" and "recap:" in off["why"]
    assert not recap(world, run).get("text")

    # Back on; a first failure pays once for a state, and a reopen retries.
    (tmp_path / ".aegis.yaml").write_text(RECAPPED)
    model = Model(fail=True)
    monkeypatch.setattr(oneshot, "run", model.run)
    view = await opened(world, run)
    model.release(0)
    await until(lambda: world.app.workflow_recaps._failed.get(run), timeout=5)
    again = await refreshed(world, run, view["view"])
    assert again["status"] == "failed"
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1
    await closed(world, run, view["view"])
    view = await opened(world, run)
    await until(lambda: len(model.calls) == 2, timeout=5)

    # Blocked: the gate and who can act are in the input, and a successful
    # text replaces the failure.
    model.fail = False
    model.release(1)
    await until(lambda: recap(world, run).get("text"), timeout=5)
    await approve(world, run)
    await until(
        lambda: any(o["kind"] == "gate" for o in world.app.workflows.wire(run)["open"]),
        timeout=15,
        what="the gate",
    )
    await refreshed(world, run, view["view"])
    await until(lambda: len(model.calls) == 3, timeout=5)
    model.release(2)
    await until(lambda: recap(world, run).get("text") == "recap 3", timeout=5)
    last = model.calls[2]["prompt"]
    assert "waiting on:" in last and "Ship it with 41?" in last and "(person)" in last
    await closed(world, run, view["view"])


MANY = """id: many
title: Many items
say: Every item on its own.
root:
  map:
    over: [a, b, c, d, e, f, g, h]
    as: x
    do: {sh: "true", id: t, say: "Run {{x}}."}
  id: m
  say: All of them.
"""


async def test_a_folded_map_still_sends_every_item(world, monkeypatch):
    model = Model()
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world, MANY)
    await done(world, run)
    await opened(world, run)
    await until(lambda: len(model.calls) == 1, timeout=5)
    model.release(0)
    await until(lambda: recap(world, run).get("text"), timeout=5)
    prompt = model.calls[0]["prompt"]
    for item in "abcdefgh":
        assert f"Run {item}." in prompt, item
    assert "items:" in prompt


async def test_two_runs_get_only_their_own_recap(world, monkeypatch):
    model = Model(texts=["for A", "for B"])
    monkeypatch.setattr(oneshot, "run", model.run)
    a = await armed(world, flow(RESULT, RESULT).replace("Two steps", "Run A"))
    b = await armed(world, flow(RESULT, RESULT).replace("Two steps", "Run B"))
    await opened(world, a)
    await opened(world, b)
    await until(lambda: len(model.calls) == 2, timeout=5)
    model.release(0)
    model.release(1)
    await until(
        lambda: recap(world, a).get("text") and recap(world, b).get("text"), timeout=5
    )
    assert recap(world, a)["text"] == "for A"
    assert recap(world, b)["text"] == "for B"
    prompts = [c["prompt"] for c in model.calls]
    assert any("Run A" in p and "Run B" not in p for p in prompts)
    assert any("Run B" in p and "Run A" not in p for p in prompts)


async def test_a_later_failure_keeps_the_saved_text_and_marks_it_stale(
    world, monkeypatch
):
    model = Model(texts=["first"], fail=False)
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world, SLOW)
    view = await opened(world, run)
    await until(lambda: len(model.calls) == 1, timeout=5)
    model.release(0)
    await until(lambda: recap(world, run).get("text") == "first", timeout=5)
    await approve(world, run)
    await until(
        lambda: world.app.workflows.runs[run].status == "done",
        timeout=20,
        what="the run's end",
    )
    model.fail = True
    await refreshed(world, run, view["view"])
    await until(lambda: len(model.calls) == 2, timeout=5)
    model.release(1)
    await until(lambda: world.app.workflow_recaps._failed.get(run), timeout=5)
    got = recap(world, run)
    assert got["text"] == "first" and got.get("stale") is True
    assert "the model is down" in (got.get("failed") or "")
    await closed(world, run, view["view"])


async def test_a_caller_without_the_run_is_refused(world, monkeypatch):
    run = await armed(world)
    agent = world.app.workflows.owner(world.app.workflows.runs[run])
    with pytest.raises(OpError) as e:
        await world.app.registry.call(
            "workflow.recap.open", {"run": run}, Caller("agent", agent.log_id)
        )
    assert e.value.code == "not_for_agents"
    monkeypatch.setattr(world.app, "sees", lambda *a, **k: False)
    with pytest.raises(OpError) as e:
        await opened(world, run)
    assert e.value.code == "not_yours"


async def test_the_saved_recap_survives_a_restart(world, monkeypatch):
    model = Model(texts=["before the restart"])
    monkeypatch.setattr(oneshot, "run", model.run)
    run = await armed(world)
    view = await opened(world, run)
    await until(lambda: len(model.calls) == 1, timeout=5)
    model.release(0)
    await until(lambda: recap(world, run).get("text"), timeout=5)
    await closed(world, run, view["view"])

    await world.restart()
    assert recap(world, run)["text"] == "before the restart"
    again = await opened(world, run)
    assert again["status"] == "current"
    await asyncio.sleep(0.05)
    assert len(model.calls) == 1


async def test_a_transport_text_field_around_the_answer_reads_as_a_paragraph(
    world, monkeypatch
):
    # Codex's agentMessage item carries the model's answer in ``text``. When
    # the answer is the JSON object the call asks for, that outer field is
    # transport and must not become the paragraph (#1194).
    async def run(argv, cwd, timeout, prompt, harness="claude-code"):
        return json.dumps(
            {
                "type": "item/completed",
                "threadId": "t",
                "turnId": "u",
                "item": {
                    "type": "agentMessage",
                    "id": "m",
                    "text": json.dumps(
                        {"text": "La revisión está pendiente."}, ensure_ascii=False
                    ),
                },
            }
        )

    monkeypatch.setattr(oneshot, "run", run)
    run_id = await armed(world)
    await opened(world, run_id)
    await until(lambda: recap(world, run_id).get("text"), timeout=5, what="the recap")
    assert recap(world, run_id)["text"] == "La revisión está pendiente."


async def test_a_recap_saved_with_the_json_wrapper_loads_as_text(world, tmp_path):
    path = tmp_path / ".aegis" / "state" / "workflow-recaps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "run-1": {
                    "input": "fingerprint",
                    "text": json.dumps(
                        {"text": "La revisión está pendiente."}, ensure_ascii=False
                    ),
                }
            }
        )
    )
    await world.restart()
    assert world.app.workflow_recaps._saved["run-1"]["text"] == (
        "La revisión está pendiente."
    )


def test_recent_activity_keeps_visible_lines_and_excludes_raw_output():
    from aegis.workflow_recap import activity

    entries = [
        {
            "kind": "tool",
            "title": "bash",
            "summary": "ran the tests",
            "detail": {"result": "RAW OUTPUT"},
        },
        {"kind": "prose", "md": "The suite is green; opening the PR."},
        {"kind": "inbox", "title": "aegis: CI is green"},
        {"kind": "system", "summary": "done in 2.1s"},
    ]
    assert activity(entries) == [
        "did: bash ran the tests",
        "said: The suite is green; opening the PR.",
        "heard: aegis: CI is green",
    ]
    assert "RAW OUTPUT" not in " ".join(activity(entries))


def test_summary_attributes_activity_to_the_task_and_attempt():
    from aegis.workflow_recap import summary

    wire = {
        "title": "Run",
        "say": "Steps.",
        "status": "running",
        "tree": [
            {
                "id": "w",
                "depth": 0,
                "say": "Implement it.",
                "state": "running",
                "attempts": 2,
            }
        ],
        "open": [],
    }
    data = summary(wire, {"w": ["did: edit src/aegis/x.py"]})
    assert data["steps"][0]["attempt"] == 3
    assert data["steps"][0]["recent"] == ["did: edit src/aegis/x.py"]


async def test_a_paused_worker_sends_its_recent_visible_activity(world, monkeypatch):
    from .test_workflows_parallel import leaf, script

    asks = script(["workflow_ask", {"question": "Which port?"}])
    model = Model(texts=["paused recap"])
    monkeypatch.setattr(oneshot, "run", model.run)
    run_id = await armed(
        world,
        f"id: ask\ntitle: Ask\nsay: One.\nroot:\n  seq:\n    - {leaf('w', 'Work.', asks)}\n  say: One.\n",
    )
    await approve(world, run_id)
    await until(
        lambda: (world.app.workflows.runs[run_id].open.get("q1") or {}).get("told"),
        timeout=20,
        what="the worker's question",
    )
    view = await opened(world, run_id)
    await until(lambda: len(model.calls) == 1, timeout=5)
    prompt = model.calls[0]["prompt"]
    assert "attempt 1, recent activity:" in prompt
    assert "workflow_ask" in prompt
    assert "did: " in prompt or "said: " in prompt
    model.release(0)
    await until(lambda: recap(world, run_id).get("text"), timeout=5)
    await closed(world, run_id, view["view"])


async def test_reload_regenerates_a_current_recap_and_keeps_the_text(
    world, monkeypatch
):
    model = Model(texts=["first", "second"])
    monkeypatch.setattr(oneshot, "run", model.run)
    run_id = await armed(world)
    view = await opened(world, run_id)
    await until(lambda: len(model.calls) == 1, timeout=5)
    model.release(0)
    await until(lambda: recap(world, run_id).get("text") == "first", timeout=5)
    assert (await refreshed(world, run_id, view["view"]))["status"] == "current"

    out = await world.app.registry.call("workflow.recap.reload", {"run": run_id})
    assert out["status"] == "loading"
    got = recap(world, run_id)
    assert got["text"] == "first" and got.get("updating") is True
    await until(lambda: len(model.calls) == 2, timeout=5)
    model.release(1)
    await until(lambda: recap(world, run_id).get("text") == "second", timeout=5)
    await closed(world, run_id, view["view"])
