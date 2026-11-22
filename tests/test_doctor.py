import dataclasses
import json
import os
import sys
from pathlib import Path

import pytest

from aegis.claude.control import Model
from aegis.harness_versions import ranges
from aegis.doctor import Found, detect, doctor, probe, propose
from aegis.roots import make_roots

GOOD = """\
default_agent: opus
agents:
  opus: {harness: claude-code, model: opus, effort: high, permission: full}
  pro: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}
queues:
  general: {agent: opus, priority: 2}
"""


def bins(
    claude: str,
    opencode: str,
    codex: str = "codex-not-installed",
    cursor: str = "cursor-not-installed",
) -> dict[str, str]:
    return {
        "claude-code": claude,
        "opencode": opencode,
        "codex": codex,
        "cursor": cursor,
    }


async def run(tmp_path: Path, text: str | None, claude: str, opencode: str):
    if text is not None:
        (tmp_path / ".aegis.yaml").write_text(text)
    return await doctor(make_roots(tmp_path, tmp_path), bins(claude, opencode))


def at(findings, where):
    return [f for f in findings if f.where == where]


async def test_a_healthy_config_has_no_errors_or_warnings(
    tmp_path, fake_claude, fake_opencode
):
    found = await run(tmp_path, GOOD, fake_claude, fake_opencode)
    assert [f for f in found if f.level != "ok"] == []
    (claude,) = at(found, "harness.claude-code")
    max_s = ranges()["claude-code"].max_s
    assert max_s in claude.message and "3 models" in claude.message
    assert {f.where for f in found} >= {
        "file",
        "harness.opencode",
        "agents.opus",
        "agents.pro",
        "default_agent",
        "queues.general",
        "state",
    }


async def test_no_file_says_run_init(tmp_path, fake_claude, fake_opencode):
    (f, *_) = await run(tmp_path, None, fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "aegis init" in f.message


async def test_a_file_that_does_not_parse_is_an_error(
    tmp_path, fake_claude, fake_opencode
):
    (f, *_) = await run(tmp_path, "agents: [1, 2\n", fake_claude, fake_opencode)
    assert (f.level, f.where) == ("error", "file") and "does not parse" in f.message


async def test_unknown_keys_warn_and_a_misspelt_field_is_named(
    tmp_path, fake_claude, fake_opencode
):
    text = "scheduler: {tick_seconds: 5}\nrecap: {agent: a}\ndefault_agent: a\nagents:\n  a: {harness: claude-code, model: opus, efort: high, permission: full}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "scheduler")[0].level == "warn"
    # A key recap.py reads, so not an unknown one; its agent is checked (_recap).
    assert [f.level for f in at(found, "recap")] == ["error"]
    assert at(found, "agents.a.efort")[0].level == "warn"
    assert at(found, "agents.a")[0].message == "effort is missing"
    assert at(found, "agents.a")[0].row == "agents.a"


async def test_a_missing_binary_is_an_error_on_the_harness_and_its_agents(
    tmp_path, fake_opencode
):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    found = await run(tmp_path, text, str(tmp_path / "no-claude"), fake_opencode)
    (h,) = at(found, "harness.claude-code")
    assert h.level == "error" and "not on PATH" in h.message
    assert at(found, "agents.a.harness")[0].level == "error"


async def test_a_model_the_catalog_does_not_list_is_a_warning(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: a\nagents:\n  a: {harness: claude-code, model: nope, effort: high, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.a.model")
    assert f.level == "warn" and "opus" in f.message


async def test_an_effort_the_model_does_not_take_is_a_warning(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: p\nagents:\n  p: {harness: opencode, model: opencode-go/fake-pro, effort: low, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.p.effort")
    assert f.level == "warn" and "high, max" in f.message


async def test_default_and_queue_problems_are_errors(
    tmp_path, fake_claude, fake_opencode
):
    text = "agents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\nqueues:\n  q: {agent: ghost}\n"
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert at(found, "default_agent")[0].level == "error"
    assert at(found, "queues.q.agent")[0].level == "error"


async def test_a_valid_recap_is_ok_and_not_an_unknown_key(
    tmp_path, fake_claude, fake_opencode
):
    found = await run(
        tmp_path, GOOD + "recap: {agent: opus}\n", fake_claude, fake_opencode
    )
    assert [f for f in found if f.level != "ok"] == []
    (r,) = at(found, "recap")
    assert r.message == "opus, opus"


async def test_a_recap_naming_an_unknown_agent_is_an_error(
    tmp_path, fake_claude, fake_opencode
):
    text = GOOD + "recap: {agent: nope}\n"
    (r,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "recap")
    assert r.level == "error" and "does not define" in r.message


async def test_a_recap_can_use_an_opencode_agent(tmp_path, fake_claude, fake_opencode):
    found = await run(
        tmp_path, GOOD + "recap: {agent: pro}\n", fake_claude, fake_opencode
    )
    (r,) = at(found, "recap")
    assert r.level == "ok" and r.message == "pro, opencode-go/fake-pro"


async def test_legacy_state_is_an_error_and_the_doctor_writes_nothing(
    tmp_path, fake_claude, fake_opencode
):
    (tmp_path / ".aegis" / "state").mkdir(parents=True)
    (tmp_path / ".aegis" / "state" / "daemon.lock").touch()
    (tmp_path / ".aegis.yaml").write_text(
        "default_agent: a\nagents:\n  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
    )
    before = sorted(
        p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p)
    )
    found = await run(tmp_path, None, fake_claude, fake_opencode)
    assert at(found, "state")[0].level == "error"
    after = sorted(
        p for p in tmp_path.rglob("*") if "bin" not in p.parts and "fake-" not in str(p)
    )
    assert before == after


async def test_detect_finds_both_fakes_with_their_models(
    tmp_path, fake_claude, fake_opencode
):
    claude, opencode, codex, cursor, _pi = await detect(
        tmp_path, bins(fake_claude, fake_opencode)
    )
    assert codex.error == "codex-not-installed is not on PATH"
    assert cursor.error == "cursor-not-installed is not on PATH"
    assert (claude.harness, claude.version, claude.error) == (
        "claude-code",
        f"{ranges()['claude-code'].max_s} (Claude Code)",
        None,
    )
    assert [m.value for m in claude.models] == ["opus", "sonnet", "haiku"]
    assert opencode.models[0].value == "opencode-go/fake-pro"


async def test_detect_probes_an_installed_cursor(
    tmp_path, fake_claude, fake_opencode, fake_cursor
):
    _, _, _, cursor, _ = await detect(
        tmp_path, bins(fake_claude, fake_opencode, cursor=fake_cursor)
    )
    assert cursor.error is None and cursor.bin == fake_cursor
    assert cursor.version and cursor.version.endswith(ranges()["cursor"].max_s)
    assert [m.value for m in cursor.models] == [
        "fake-pro",
        "fake-flash",
        "fake-reason",
    ]


def m(value: str, efforts=(), free=False) -> Model:
    return Model(
        value=value,
        resolved=value,
        label=value,
        doc="",
        efforts=tuple(efforts),
        free=free,
    )


def oc(*models: Model) -> Found:
    return Found("opencode", "/bin/opencode", "1.18", models)


CLAUDE = Found("claude-code", "/bin/claude", "2.1", (m("opus"),))


@pytest.fixture
def scores(monkeypatch):
    from aegis.ranking import Ranking

    def use(scores: dict) -> None:
        ranked = Ranking.from_dict({"models": scores})
        monkeypatch.setattr("aegis.doctor.ranking", lambda: ranked)

    return use


def test_propose_orders_free_models_by_ranking(scores):
    # Coding Index the other way round: Intelligence orders.
    scores({
        "o/a": {"intelligence": 41.2, "coding": 10.0},
        "o/b": {"intelligence": 38.9, "coding": 20.0},
    })  # fmt: skip
    doc = propose([oc(m("o/c", free=True), m("o/a", free=True), m("o/b", free=True))])
    (free,) = doc.agents
    assert (free.name, free.harness, free.permission) == ("free", "opencode", "full")
    assert free.models == ["o/a", "o/b", "o/c"] and free.model == "o/a"


def test_propose_puts_no_paid_model_in_a_cascade(scores):
    scores({"g/pro": {"intelligence": 60.0}, "o/pickle": {"intelligence": 20.0}})
    paid = m("g/pro", ("high",))
    doc = propose([oc(paid, m("o/pickle", free=True), m("g/max"))])
    (free,) = doc.agents
    assert (free.model, free.models) == ("o/pickle", [])


def test_propose_with_only_paid_models_proposes_the_best_one_alone(scores):
    """Nothing free: the best model alone, as before cascades, so an install
    with only paid OpenCode models still gets an agent."""
    scores({"g/pro": {"intelligence": 60.0}})
    doc = propose([oc(m("g/max"), m("g/pro", ("high",)))])
    (agent,) = doc.agents
    assert (agent.name, agent.model, agent.models) == ("pro", "g/pro", [])
    assert [q.name for q in doc.queues] == ["general"]
    assert [a.name for a in propose([CLAUDE, oc(m("g/pro"))]).agents] == ["opus", "pro"]


def test_propose_with_claude_adds_free_beside_opus_and_a_light_queue():
    doc = propose([CLAUDE, oc(m("o/a", free=True), m("o/b", free=True))])
    assert [
        (a.name, a.harness, a.model, a.effort, a.permission) for a in doc.agents
    ] == [
        ("opus", "claude-code", "opus", "high", "full"),
        ("free", "opencode", "o/a", "high", "full"),
    ]
    assert doc.default_agent == "opus"
    assert [(q.name, q.agent, q.priority) for q in doc.queues] == [
        ("general", "opus", None),
        ("light", "free", None),
    ]


def test_propose_without_claude_makes_free_the_default():
    doc = propose(
        [Found("claude-code", None), oc(m("o/a", free=True), m("o/b", free=True))]
    )
    assert [a.name for a in doc.agents] == ["free"] and doc.default_agent == "free"
    assert [(q.name, q.agent) for q in doc.queues] == [("general", "free")]
    assert propose([Found("claude-code", None), Found("opencode", None)]).agents == []


def test_one_free_model_is_a_cascade_of_one():
    (free,) = propose([oc(m("o/pickle", free=True))]).agents
    assert (free.model, free.models) == ("o/pickle", [])


def test_propose_takes_effort_from_the_first_model(scores):
    scores({"x/y": {"intelligence": 50.0}})
    doc = propose([oc(m("g/flash", ("low", "medium", "high"), free=True))])
    assert doc.agents[0].effort == "high"
    doc = propose(
        [
            oc(
                m("g/flash", ("high",), free=True),
                m("x/y", ("minimal", "low"), free=True),
            )
        ]
    )
    assert doc.agents[0].effort == "low", "x/y ranks first, so its efforts choose"


async def test_an_opencode_older_than_the_range_warns_with_the_upgrade(
    tmp_path, fake_claude
):
    old = tmp_path / "old" / "opencode"
    old.parent.mkdir()
    old.write_text("#!/bin/sh\necho 1.19.0\n")
    old.chmod(0o755)
    found = await run(tmp_path, GOOD, fake_claude, str(old))
    (oc,) = [f for f in at(found, "harness.opencode") if f.level == "warn"]
    assert "OpenCode 1.19.0 is older than" in oc.message
    assert f"found: {old}" in oc.message
    assert "@opencode/cli@" in oc.message


async def test_a_model_listed_twice_is_a_warning_and_the_repeat_is_dropped(
    tmp_path, fake_claude, fake_opencode
):
    text = "default_agent: p\nagents:\n  p: {harness: opencode, models: [opencode-go/fake-pro, opencode-go/fake-flash, opencode-go/fake-pro], effort: high, permission: full}\n"
    (f,) = at(await run(tmp_path, text, fake_claude, fake_opencode), "agents.p.models")
    assert f.level == "warn"
    assert f.message == "opencode-go/fake-pro is listed twice; the second is ignored"


async def test_a_cascade_out_of_ranking_order_warns(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    from aegis.ranking import Ranking

    # Coding Index the other way round: Intelligence orders.
    scores = {
        "opus": {"intelligence": 57.6, "coding": 30.0},
        "sonnet": {"intelligence": 52.1, "coding": 45.0},
    }
    monkeypatch.setattr(
        "aegis.doctor.ranking", lambda: Ranking.from_dict({"models": scores})
    )
    agent = (
        "  {}: {{harness: claude-code, models: [{}], effort: high, permission: full}}\n"
    )
    text = "default_agent: down\nagents:\n" + "".join(
        agent.format(name, models)
        for name, models in (
            ("down", "sonnet, opus"),
            ("unranked", "haiku, opus"),
            ("ordered", "opus, sonnet, haiku"),
        )
    )
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    (f,) = at(found, "agents.down.models")
    assert f.level == "warn" and f.row == "agents.down"
    assert f.message == "sonnet (52.1) is above opus (57.6) in the ranking"
    (f,) = at(found, "agents.unranked.models")
    assert f.message == "haiku (unranked) is above opus (57.6) in the ranking"
    assert at(found, "agents.ordered.models") == []


FULL_UNIT = {
    "AmbientCapabilities": "CAP_SETUID CAP_SETGID CAP_SETPCAP",
    "CapabilityBoundingSet": "CAP_SETUID CAP_SETGID CAP_SETPCAP",
}


def test_knowledge_in_use_without_git_warns(tmp_path, monkeypatch):
    from aegis.doctor import _knowledge

    roots = make_roots(tmp_path, tmp_path)
    monkeypatch.setattr("aegis.doctor.shutil.which", lambda b: None)
    assert _knowledge(roots) == []  # no knowledge yet: nothing to say
    (roots.state_root / "knowledge" / "ana").mkdir(parents=True)
    [f] = _knowledge(roots)
    assert (f.level, f.where) == ("warn", "knowledge") and "git" in f.message
    monkeypatch.setattr("aegis.doctor.shutil.which", lambda b: "/usr/bin/git")
    assert _knowledge(roots) == []


async def test_a_failing_version_read_warns_unknown_and_still_lists_models(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    """A harness whose --version fails is judged unknown, as a launch judges it,
    and its models are still read (#454)."""
    monkeypatch.setenv("AEGIS_FAKE_HARNESS_VERSION", "")
    found = await run(tmp_path, GOOD, fake_claude, fake_opencode)
    claude = at(found, "harness.claude-code")
    assert [f.level for f in claude] == ["warn"], claude
    assert "could not be read" in claude[0].message
    assert "--version` failed" in claude[0].message


def go_config(tmp_path: Path, extra: str = "", pool: str = "opencode-go") -> str:
    return (
        "agents:\n  go:\n    harness: claude-code\n    model: deepseek-v4.1-flash\n"
        "    effort: high\n    permission: full\n"
        "    base_url: https://opencode.ai/inference/go/anthropic\n"
        f"    pool: {pool}\n" + extra
    )


def go_key(monkeypatch, key: str | None) -> None:
    """The Go pool's own key, as a session start reads it."""
    import aegis.quota as quota

    monkeypatch.setattr(
        quota,
        "PROVIDERS",
        tuple(
            dataclasses.replace(p, inference_key=lambda: key)
            if p.name == "opencode-go"
            else p
            for p in quota.PROVIDERS
        ),
    )


def key_file(tmp_path: Path, mode: int = 0o600) -> Path:
    k = tmp_path / "go.key"
    k.write_text("sk-test")
    k.chmod(mode)
    return k


async def test_a_connection_is_ok_without_a_catalog_row(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    go_key(monkeypatch, "sk-go")
    found = await run(
        tmp_path,
        go_config(tmp_path, "    isolated: true\n"),
        fake_claude,
        fake_opencode,
    )
    rows = [f for f in found if f.row == "agents.go"]
    assert [(f.level, f.message) for f in rows] == [
        ("ok", "claude-code deepseek-v4.1-flash at opencode.ai, pool opencode-go")
    ]


async def test_a_connection_without_the_pools_key_is_an_error(
    tmp_path, fake_claude, fake_opencode, monkeypatch
):
    go_key(monkeypatch, None)
    found = await run(
        tmp_path,
        go_config(tmp_path, "    isolated: true\n"),
        fake_claude,
        fake_opencode,
    )
    (f,) = at(found, "agents.go.api_key_file")
    assert f.level == "error" and "OpenCode Go holds no key" in f.message


@pytest.mark.parametrize(
    "setup, fragment",
    [
        (lambda p: p / "missing.key", "does not exist"),
        (lambda p: key_file(p, 0o644), "must be mode 0600"),
    ],
)
async def test_a_key_file_that_cannot_be_used_is_an_error(
    tmp_path, fake_claude, fake_opencode, setup, fragment
):
    path = setup(tmp_path)
    text = go_config(tmp_path, f"    isolated: true\n    api_key_file: {path}\n")
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    (f,) = at(found, "agents.go.api_key_file")
    assert f.level == "error" and fragment in f.message


async def test_pool_none_warns_and_needs_a_key_file(
    tmp_path, fake_claude, fake_opencode
):
    found = await run(
        tmp_path,
        go_config(tmp_path, "    isolated: true\n", pool="none"),
        fake_claude,
        fake_opencode,
    )
    (key,) = at(found, "agents.go.api_key_file")
    assert key.level == "error" and "pool none has none of its own" in key.message
    (warn,) = at(found, "agents.go.pool")
    assert warn.level == "warn" and "no gauge" in warn.message


async def test_a_connection_warns_for_isolation_guard_and_usd_budget(
    tmp_path, fake_claude, fake_opencode
):
    k = key_file(tmp_path)
    text = go_config(
        tmp_path,
        f"    api_key_file: {k}\n"
        "    quota: {claude: {weekly_all: 80}}\n"
        "    budget: [{usd: 5, per: session}]\n",
    )
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    levels = {f.where: f.level for f in found if f.row == "agents.go"}
    assert levels == {
        "agents.go.isolated": "warn",
        "agents.go.quota": "warn",
        "agents.go.budget": "warn",
    }
    (guard,) = at(found, "agents.go.quota")
    assert "names claude" in guard.message
    assert not at(found, "agents.go.model")  # no catalog comparison


async def test_a_tokens_budget_and_its_own_pool_guard_do_not_warn(
    tmp_path, fake_claude, fake_opencode
):
    k = key_file(tmp_path)
    text = go_config(
        tmp_path,
        f"    isolated: true\n    api_key_file: {k}\n"
        "    quota: {opencode-go: {weekly: 80}}\n"
        "    budget: [{usd: 5, per: session}, {tokens: 1000000, per: 7d}]\n",
    )
    found = await run(tmp_path, text, fake_claude, fake_opencode)
    assert [f.level for f in found if f.row == "agents.go"] == ["ok"]


# The probe spends a call on the agent's account, so it never meets a real
# Claude Code here: this binary records how it was started and prints what
# FAKE_PROBE_REPLY holds.
FAKE_PROBE = """\
import json, os, sys
prompt = sys.stdin.read()
with open(os.environ["FAKE_PROBE_LOG"], "w") as f:
    json.dump({"argv": sys.argv[1:], "env": dict(os.environ), "stdin": prompt,
               "cwd": os.getcwd()}, f)
sys.stdout.write(os.environ["FAKE_PROBE_REPLY"])
"""


@pytest.fixture
def fake_probe(tmp_path, monkeypatch):
    """The fake binary's path, and a reader of what it was started with."""
    script = tmp_path / "fake_probe.py"
    script.write_text(FAKE_PROBE)
    binary = tmp_path / "probe-bin" / "claude"
    binary.parent.mkdir()
    binary.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    binary.chmod(0o755)
    log = tmp_path / "probe.json"
    monkeypatch.setenv("FAKE_PROBE_LOG", str(log))
    # What the server's own environment would leak into the call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-server")

    def reply(res: dict | str) -> None:
        monkeypatch.setenv(
            "FAKE_PROBE_REPLY", res if isinstance(res, str) else json.dumps(res)
        )

    return str(binary), reply, lambda: json.loads(log.read_text())


async def run_probe(tmp_path: Path, binary: str, extra: str = "", name: str = "go"):
    k = key_file(tmp_path)
    text = go_config(
        tmp_path,
        f"    isolated: true\n    api_key_file: {k}\n"
        "    env: {API_TIMEOUT_MS: '600000'}\n" + extra,
    )
    (tmp_path / ".aegis.yaml").write_text(text)
    roots = make_roots(tmp_path, tmp_path)
    return await probe(roots, bins(binary, "opencode-not-installed"), name)


async def test_the_probe_starts_claude_code_on_the_connection(tmp_path, fake_probe):
    binary, reply, started = fake_probe
    reply({"type": "result", "is_error": False, "result": "ok"})
    f = await run_probe(tmp_path, binary)
    assert (f.level, f.where, f.row) == ("ok", "agents.go", "agents.go")
    assert f.message == (
        "Claude Code answered at opencode.ai (deepseek-v4.1-flash, pool opencode-go)"
    )
    seen = started()
    assert seen["argv"] == [
        "-p", "--model", "deepseek-v4.1-flash", "--output-format", "json",
        "--tools", "", "--setting-sources", "",
    ]  # fmt: skip
    assert seen["stdin"] == "Reply with the word ok."
    env = seen["env"]
    assert env["ANTHROPIC_BASE_URL"] == "https://opencode.ai/inference/go/anthropic"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "sk-test"
    assert env["API_TIMEOUT_MS"] == "600000"  # the agent's env
    assert "ANTHROPIC_API_KEY" not in env and "CLAUDE_CODE_OAUTH_TOKEN" not in env
    # A scratch config directory, gone once the probe is over.
    assert env["CLAUDE_CONFIG_DIR"] == seen["cwd"]
    assert not os.path.exists(seen["cwd"])


async def test_an_error_result_is_the_endpoint_refusing(tmp_path, fake_probe):
    binary, reply, _ = fake_probe
    reply({"is_error": True, "result": "API Error: 401 invalid api key"})
    f = await run_probe(tmp_path, binary)
    assert f.level == "error"
    assert f.message == "the endpoint refused the call: API Error: 401 invalid api key"


async def test_a_missing_session_header_is_named(tmp_path, fake_probe):
    """What a Claude Code release that stops sending x-claude-code-session-id
    would show."""
    binary, reply, _ = fake_probe
    reply(
        {
            "is_error": True,
            "result": 'API Error: 400 {"type":"error","error":'
            '{"type":"MissingSessionID","message":"missing session id"}}',
        }
    )
    f = await run_probe(tmp_path, binary)
    assert (f.level, f.message) == (
        "error",
        "the endpoint refused the call: 400 MissingSessionID",
    )


async def test_no_answer_is_an_error(tmp_path, fake_probe):
    binary, reply, _ = fake_probe
    reply("not json")
    f = await run_probe(tmp_path, binary)
    assert f.level == "error" and "no answer" in f.message


async def test_the_probe_starts_nothing_without_a_key(tmp_path, fake_probe):
    binary, reply, started = fake_probe
    reply({"is_error": False, "result": "ok"})
    text = go_config(tmp_path, f"    api_key_file: {tmp_path / 'missing.key'}\n")
    (tmp_path / ".aegis.yaml").write_text(text)
    f = await probe(make_roots(tmp_path, tmp_path), bins(binary, "oc"), "go")
    assert (f.level, f.where) == ("error", "agents.go.api_key_file")
    assert "does not exist" in f.message
    assert not (tmp_path / "probe.json").exists()


async def test_the_probe_refuses_an_agent_without_a_connection(tmp_path, fake_probe):
    binary, _, _ = fake_probe
    (tmp_path / ".aegis.yaml").write_text(GOOD)
    roots = make_roots(tmp_path, tmp_path)
    f = await probe(roots, bins(binary, "oc"), "opus")
    assert f.level == "error" and "base_url" in f.message
    f = await probe(roots, bins(binary, "oc"), "nobody")
    assert f.level == "error" and "no agent 'nobody'" in f.message
    assert not (tmp_path / "probe.json").exists()
