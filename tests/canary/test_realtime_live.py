"""Realtime delivery in the published agent CLIs, with a localhost model."""

import json

import pytest

from ..conftest import until
from .fakemodel import Call, Rule, Say, Shell

pytestmark = pytest.mark.live


@pytest.mark.parametrize("setup", ["claude-code", "codex"], indirect=True)
async def test_realtime_steers_a_running_live_harness(canary, monkeypatch):
    from aegis.transcript.store import read_store

    requests = []
    record = canary.model._record

    def observe(api, body, conversation, reply):
        requests.append(json.dumps(body.get("messages") or body.get("input") or []))
        record(api, body, conversation, reply)

    monkeypatch.setattr(canary.model, "_record", observe)
    canary.model.script(
        Rule("CANARY-VOICE-BUSY", [Shell("sleep 4"), Call("meta"), Say("busy done")]),
        Rule("CANARY-VOICE-BLOCK", [Say("voice received")]),
    )
    session = await canary.spawn()
    await canary.send(session, "CANARY-VOICE-BUSY")
    await until(
        lambda: any(
            e["kind"] == "tool" and e["status"] == "running" for e in session.entries()
        ),
        timeout=60,
        what="the live harness's running shell tool",
    )

    call = canary.app.registry.call
    params = {"log_id": session.log_id, "realtime": {"id": "live-mic", "state": "open"}}
    block = {**params, "text": "CANARY-VOICE-BLOCK", "nonce": "live-mic:0"}
    await call("session.send", block)
    await call("session.send", block)
    sent, _ = read_store(session.store.path)
    (voice,) = [r for r in sent if r.get("kind") == "send" and r.get("inbox")]
    assert voice["steered"] and voice["inbox"][0]["by_person"]
    assert "files" not in voice
    await until(
        lambda: any("CANARY-VOICE-BLOCK" in body for body in requests),
        timeout=25,
        what="the voice block to reach the model through the real CLI",
    )
    await until(
        lambda: session.status == "idle", timeout=60, what="the steered turn to end"
    )
    await call(
        "session.send",
        {
            "log_id": session.log_id,
            "nonce": "live-mic:1",
            "realtime": {"id": "live-mic", "state": "ended"},
        },
    )
    await until(
        lambda: any(
            "realtime capture ended" in x["last_person"] for x in canary.model.log
        ),
        timeout=60,
        what="the capture-ended notification to reach the model",
    )
    voice_rows = [e for e in session.entries() if e["detail"].get("realtime")]
    assert [(e["md"], e["detail"]["capture_state"]) for e in voice_rows] == [
        ("CANARY-VOICE-BLOCK", "open"),
        ("", "ended"),
    ]
