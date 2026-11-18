"""SignIns (signin/core.py): a failed sign-in drives the harness's own login in a
terminal that stores nothing, against tests/fake_login.py."""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aegis.signin import core
from aegis.signin.core import SignIns
from aegis.signin.recipes import load_recipes
from aegis.terminals import Terminals

from .conftest import until

FAKE = Path(__file__).parent / "fake_login.py"


class FakeSession:
    def __init__(self, handle: str, linux_user: str | None = None) -> None:
        self.log_id = f"log-{handle}"
        self.handle = handle
        self.spec = SimpleNamespace(linux_user=linux_user, harness="claude-code")
        self.archived = False
        self.records: list[dict] = []
        self.delivered: list[tuple[str, str]] = []
        self.restarts = 0

    def record_card(self, rec: dict) -> None:
        self.records.append(rec)

    def entries(self) -> list[dict]:
        last: dict[str, dict] = {}
        for r in self.records:
            if r["kind"] == "sign_in":
                last[r["card_id"]] = r
        return [{"kind": "sign_in", "detail": r} for r in last.values()]

    def card(self) -> dict | None:
        cards = [r for r in self.records if r["kind"] == "sign_in"]
        return cards[-1] if cards else None

    async def restart(self) -> None:
        self.restarts += 1

    async def deliver(self, head: str, body: str) -> None:
        self.delivered.append((head, body))


class Bus:
    def __init__(self) -> None:
        self.msgs: list = []

    def __call__(self, channel, ops) -> None:
        self.msgs.append((channel, ops))


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "TICK_S", 0.05)
    monkeypatch.setattr(core, "UNKNOWN_SCREEN_S", 1.0)
    bus = Bus()
    terms = Terminals(tmp_path / "state", tmp_path, lambda: {}, bus)
    recipes = load_recipes()
    recipes["claude-code"].cmd = [sys.executable, str(FAKE)]
    token_file = tmp_path / "token"
    toasts: list[str] = []

    async def check(found: dict) -> str | None:
        if not found["token"].startswith("sk-ant-oat01-FAKE"):
            return "Claude Code refused it: 401"
        token_file.write_text(found["token"])
        return None

    a, b = FakeSession("alpha"), FakeSession("beta")
    sessions = {a.log_id: a, b.log_id: b}
    spawns: list[str] = []
    real = terms.spawn_ephemeral

    async def counted(*args, **kw):
        t = await real(*args, **kw)
        spawns.append(t.id)
        return t

    terms.spawn_ephemeral = counted
    sign_ins = SignIns(
        sessions,
        terms,
        lambda: recipes,
        lambda text, s: toasts.append(text),
        {"claude-code": check},
    )
    w = SimpleNamespace(
        sign_ins=sign_ins, a=a, b=b, sessions=sessions, terms=terms, bus=bus,
        token_file=token_file, toasts=toasts, spawns=spawns, recipes=recipes,
        state=tmp_path / "state", check=check,
    )  # fmt: skip
    yield w


async def until_step(s: FakeSession, step: str, timeout: float = 8.0) -> dict:
    await until(
        lambda: (c := s.card()) and c["step"] == step, timeout=timeout, what=step
    )
    return s.card()


def published(w) -> str:
    return json.dumps(w.bus.msgs, ensure_ascii=False) + json.dumps(
        [w.a.records, w.b.records, w.a.delivered, w.b.delivered], ensure_ascii=False
    )


async def test_a_code_typed_on_the_card_ends_in_a_token_and_restarts(world):
    w = world
    w.sign_ins.failed(w.a, "claude-code")
    w.sign_ins.failed(w.b, "claude-code")
    card = await until_step(w.a, "paste")
    assert card["url"].startswith("https://claude.com/cai/oauth/authorize?")
    assert card["handles"] == ["alpha", "beta"] and w.b.card() is None
    await w.sign_ins.answer(card["card_id"], "FAKECODE")
    await until(
        lambda: any(r["kind"] == "sign_in_done" for r in w.a.records), timeout=8
    )
    done = next(r for r in w.a.records if r["kind"] == "sign_in_done")
    assert set(done["restarted"]) == {"alpha", "beta"}
    assert w.a.restarts == w.b.restarts == 1
    assert "aegis:login · renewed" in w.a.delivered[0][0]
    assert w.token_file.read_text().startswith("sk-ant-oat01-FAKE")
    assert w.a.card()["step"] == "done"
    assert "sk-ant-oat01-FAKE" not in published(w)
    assert "••••" in published(w)  # the token was on screen, masked
    assert not list(w.state.glob("**/*")), "a sign-in terminal leaves nothing on disk"
    assert w.terms.archived == {}
    assert len(w.toasts) == 1


async def test_two_failures_start_one_terminal(world):
    w = world
    w.sign_ins.failed(w.a, "claude-code")
    w.sign_ins.failed(w.b, "claude-code")
    w.sign_ins.failed(w.a, "claude-code")
    await until_step(w.a, "paste")
    assert len(w.spawns) == 1 and len(w.toasts) == 1


async def test_a_wrong_code_fails_the_card_and_the_sessions_keep_waiting(world):
    w = world
    w.sign_ins.failed(w.a, "claude-code")
    card = await until_step(w.a, "paste")
    await w.sign_ins.answer(card["card_id"], "WRONG")
    failed = await until_step(w.a, "failed")
    assert failed["error"] and "OAuth error" in failed["tail"]
    assert w.a.restarts == 0
    assert w.sign_ins.waiting["claude-code"] == [w.a.log_id]
    again = await w.sign_ins.start("claude-code", w.a, "alpha")
    assert again != card["card_id"]
    assert (await until_step(w.a, "paste"))["handles"] == ["alpha"]


async def test_cancel_stops_the_terminal(world):
    w = world
    w.sign_ins.failed(w.a, "claude-code")
    card = await until_step(w.a, "paste")
    await w.sign_ins.cancel(card["card_id"])
    assert w.a.card()["step"] == "failed" and w.a.card()["error"] == "cancelled"
    await until(
        lambda: not w.terms.open and not w.terms.closing, timeout=8, what="stopped"
    )


async def test_a_restart_ends_an_open_card_as_interrupted(world):
    w = world
    w.sign_ins.failed(w.a, "claude-code")
    await until_step(w.a, "paste")
    await w.sign_ins.shutdown()
    after = SignIns(
        w.sessions,
        w.terms,
        lambda: w.recipes,
        lambda t, s: None,
        {"claude-code": w.check},
    )
    after.boot()
    assert w.a.card()["step"] == "failed"
    assert w.a.card()["error"] == "interrupted: start again"


async def test_a_screen_no_rule_knows_fails_as_unknown(world, monkeypatch):
    w = world
    monkeypatch.setenv("FAKE_LOGIN_SILENT", "1")
    w.sign_ins.failed(w.a, "claude-code")
    card = await until_step(w.a, "failed", timeout=8)
    assert card["error"] == "unknown screen"


async def test_opencode_go_is_chosen_then_its_key_is_checked(world):
    w = world
    checked: list = []

    async def oc_check(found: dict) -> str | None:
        checked.append(found)
        return None

    w.recipes["opencode"].cmd = [sys.executable, str(FAKE), "opencode"]
    w.sign_ins._checks["opencode"] = oc_check
    w.a.spec.harness = "opencode"
    w.sign_ins.failed(w.a, "opencode")
    card = await until_step(w.a, "choose")
    assert card["options"] == ["OpenCode Go", "OpenCode Console"]
    with pytest.raises(ValueError):
        await w.sign_ins.choose(card["card_id"], "Anthropic")
    await w.sign_ins.choose(card["card_id"], "OpenCode Go")
    card = await until_step(w.a, "paste")
    assert card["prompt"] == "Paste your OpenCode Go API key." and "url" not in card
    await w.sign_ins.answer(card["card_id"], "sk-FAKEKEY")
    await until(
        lambda: any(r["kind"] == "sign_in_done" for r in w.a.records), timeout=8
    )
    assert checked == [{"token": None}]
    assert w.a.card()["step"] == "done" and w.a.restarts == 1
