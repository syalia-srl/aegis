from pathlib import Path

import pytest

from aegis.agents import ConfigError, load_agents, model_suggestions, resolve
from aegis.ops import OpError
from aegis.queues import load_queues
from aegis.quota_gate import pool_of
from aegis.session import Connection


def write(tmp_path: Path, text: str) -> Path:
    (tmp_path / ".aegis.yaml").write_text(text)
    return tmp_path


def by_name(root: Path) -> dict:
    return {a.name: a for a in load_agents(root)}


def test_the_three_harness_forms_parse(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  flat: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  str: {provider: claude-code, model: opus, effort: high, permission: full}\n"
            "  nested:\n"
            "    provider: {name: claude-code, model: haiku, effort: low, permission: read}\n",
        )
    )
    assert all(a.enabled and a.error is None for a in agents.values())
    n = agents["nested"]
    assert (n.harness, n.model, n.effort, n.permission) == (
        "claude-code",
        "haiku",
        "low",
        "read",
    )


def test_a_missing_field_disables_only_that_agent(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  ok: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  noeffort: {harness: claude-code, model: opus, permission: full}\n"
            "  bare: {}\n",
        )
    )
    assert agents["ok"].enabled
    assert agents["noeffort"].error == "effort is missing"
    assert not agents["noeffort"].enabled
    assert agents["bare"].error == "harness, model, effort, permission are missing"


def test_an_empty_string_counts_as_missing(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            'agents:\n  x: {harness: claude-code, model: "", effort: high, permission: full}\n',
        )
    )
    assert a.error == "model is missing"


def test_a_value_outside_the_vocabulary_is_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  x: {harness: claude-code, model: opus, effort: huge, permission: full}\n",
        )
    )
    assert a.error == "effort 'huge' is not one of low, medium, high, xhigh, max"


def test_an_unsupported_harness_is_disabled_but_not_an_error(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  d: {provider: lovelaice, model: x, effort: high, permission: full}\n",
        )
    )
    assert a.error is None and not a.enabled


def test_priming_is_optional_and_never_listed(tmp_path):
    agents = by_name(
        write(
            tmp_path,
            "agents:\n"
            "  plain: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev:\n"
            "    harness: claude-code\n    model: opus\n    effort: max\n    permission: read\n"
            "    priming: |\n      You review.\n      Rank by severity.\n",
        )
    )
    assert agents["plain"].priming is None
    assert agents["rev"].priming == "You review.\nRank by severity.\n"
    d = agents["rev"].as_dict()
    assert d["has_priming"] is True and "priming" not in d


def test_no_config_means_no_agents(tmp_path):
    assert load_agents(tmp_path) == []


def test_malformed_config_names_the_file(tmp_path):
    with pytest.raises(ConfigError, match=r"\.aegis\.yaml"):
        load_agents(write(tmp_path, "agents: [1, 2\n"))


def test_model_suggestions_are_the_models_agents_name_without_duplicates(tmp_path):
    agents = load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  a: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  b: {harness: claude-code, model: claude-sonnet-5, effort: high, permission: full}\n"
            "  c: {harness: opencode, model: opencode-go/x, effort: high, permission: full}\n"
            "  d: {harness: claude-code, model: opus, effort: low, permission: read}\n",
        )
    )
    assert model_suggestions(agents) == {
        "claude-code": ["opus", "claude-sonnet-5"],
        "opencode": ["opencode-go/x"],
        "codex": [],
        "cursor": [],
        "pi": [],
    }


@pytest.fixture
def agents(tmp_path):
    return load_agents(
        write(
            tmp_path,
            "agents:\n"
            "  opus: {harness: claude-code, model: opus, effort: high, permission: full}\n"
            "  rev: {harness: claude-code, model: opus, effort: max, permission: read, priming: You review.}\n"
            "  broken: {harness: claude-code, model: opus, permission: full}\n"
            "  deep: {harness: opencode, model: opencode-go/fake-pro, effort: high, permission: full}\n"
            "  lovelaice-agent: {harness: lovelaice, model: m, effort: high, permission: full}\n",
        )
    )


def none() -> dict:
    return {"harness": None, "model": None, "effort": None, "permission": None}


def test_resolve_takes_the_agents_fields_and_its_priming(agents, tmp_path):
    spec = resolve(agents, None, "rev", none(), tmp_path)
    assert (spec.agent, spec.model, spec.effort, spec.permission) == (
        "rev",
        "opus",
        "max",
        "read",
    )
    assert (spec.harness, spec.priming, spec.overridden) == (
        "claude-code",
        "You review.",
        (),
    )


def test_an_override_replaces_a_field_and_is_recorded(agents, tmp_path):
    spec = resolve(
        agents,
        None,
        "opus",
        none() | {"model": "sonnet", "effort": "high"},
        tmp_path,
        spawned_by="p",
    )
    # effort equals the agent's value, so it is not an override.
    assert (spec.model, spec.overridden, spec.spawned_by) == ("sonnet", ("model",), "p")


def test_no_name_falls_back_to_default_agent(agents, tmp_path):
    assert resolve(agents, "rev", None, none(), tmp_path).agent == "rev"


@pytest.mark.parametrize(
    ("default", "name", "overrides", "code"),
    [
        (None, None, {}, "no_agent"),
        (None, "nope", {}, "unknown_agent"),
        (None, "broken", {}, "bad_agent"),
        ("broken", None, {}, "bad_agent"),
        (None, "lovelaice-agent", {}, "harness_unsupported"),
        (None, "opus", {"harness": "opencode"}, "bad_model"),
    ],
)
def test_resolve_errors(agents, tmp_path, default, name, overrides, code):
    with pytest.raises(OpError) as e:
        resolve(agents, default, name, none() | overrides, tmp_path)
    assert e.value.code == code


def test_bad_agent_says_what_is_wrong(agents, tmp_path):
    with pytest.raises(OpError, match="agent 'broken': effort is missing"):
        resolve(agents, None, "broken", none(), tmp_path)


def test_a_queue_names_its_agent_and_a_priority_from_zero_up(tmp_path):
    root = write(
        tmp_path,
        "queues:\n"
        "  ok: {agent: opus, priority: 3}\n"
        "  plain: {agent: opus}\n"
        "  paused: {agent: opus, priority: 0}\n"
        "  old: {agent: opus, max_parallel: 2}\n"
        "  bare: {}\n"
        "  below: {agent: opus, priority: -1}\n"
        "  word: {agent: opus, priority: high}\n",
    )
    ok = {
        "agent": "opus",
        "quiet": True,
        "legacy": [],
        "guard": None,
        "max_pending": None,
    }
    assert load_queues(root) == {
        "ok": ok | {"priority": 3},
        "plain": ok | {"priority": 1},
        "paused": ok | {"priority": 0},
        "old": ok | {"priority": 1, "legacy": ["max_parallel"]},
        "bare": {"error": "agent is missing"},
        "below": {"error": "priority -1 is not a whole number from 0 up"},
        "word": {"error": "priority 'high' is not a whole number from 0 up"},
    }


def test_an_opencode_agent_names_provider_and_model(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  d: {provider: opencode, model: x, effort: high, permission: full}\n",
        )
    )
    assert (
        a.error
        == "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"
    )
    assert not a.enabled


PRODUCT = (
    "agents:\n"
    "  product: {harness: claude-code, model: opus, effort: high, permission: full,\n"
    "            harness_tools: false, isolated: true, primer: false, notices: false}\n"
    "  bad: {harness: claude-code, model: opus, effort: high, permission: full,\n"
    "        isolated: 'yes'}\n"
)


def test_the_product_switches_parse_and_reach_the_spec(tmp_path):
    agents = load_agents(write(tmp_path, PRODUCT))
    spec = resolve(agents, None, "product", none(), tmp_path)
    assert (spec.harness_tools, spec.isolated, spec.primer, spec.notices) == (
        False,
        True,
        False,
        False,
    )
    bad = {a.name: a for a in agents}["bad"]
    assert bad.error == "isolated must be true or false" and not bad.enabled


GO = (
    "  go:\n"
    "    harness: claude-code\n"
    "    model: deepseek-v4.1-flash\n"
    "    effort: high\n"
    "    permission: full\n"
)


def test_a_connection_parses_with_its_pool_key_file_and_env(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n"
            + GO
            + "    base_url: https://opencode.ai/inference/go/anthropic\n"
            "    pool: opencode-go\n"
            "    api_key_file: ~/.config/aegis/go.key\n"
            "    env: {ANTHROPIC_DEFAULT_HAIKU_MODEL: deepseek-v4.1-flash}\n",
        )
    )
    assert a.error is None and a.enabled
    assert a.base_url == "https://opencode.ai/inference/go/anthropic"
    assert a.pool == "opencode-go"
    assert a.api_key_file == str(Path("~/.config/aegis/go.key").expanduser())
    assert a.env == {"ANTHROPIC_DEFAULT_HAIKU_MODEL": "deepseek-v4.1-flash"}


def test_pool_none_needs_no_key_file_to_parse(tmp_path):
    (a,) = load_agents(
        write(tmp_path, "agents:\n" + GO + "    base_url: http://gw.local/a\n"
              "    pool: none\n")
    )  # fmt: skip
    assert a.error is None and (a.pool, a.api_key_file, a.env) == ("none", "", {})


def test_an_agent_without_a_connection_parses_as_before(tmp_path):
    (a,) = load_agents(write(tmp_path, "agents:\n" + GO))
    assert a.error is None
    assert (a.base_url, a.pool, a.api_key_file, a.env) == ("", "", "", {})


@pytest.mark.parametrize(
    "extra, fragment",
    [
        ("    base_url: not a url\n    pool: none\n", "is not an http or https URL"),
        ("    base_url: ftp://x.y/a\n    pool: none\n", "is not an http or https URL"),
        ("    base_url: https://${HOST}/a\n    pool: none\n", "takes no ${VAR}"),
        ("    base_url: https://me:secret@x.y/a\n    pool: none\n",
         "must not hold a user or password"),
        ("    base_url: https://x.y/a\n", "base_url needs a pool"),
        ("    base_url: https://x.y/a\n    pool: codex\n", "pool codex has no gauge"),
        ("    base_url: https://x.y/a\n    pool: claude\n", "pool claude would read"),
        ("    base_url: https://x.y/a\n    pool: nope\n", "pool 'nope' is not one of"),
        ("    pool: opencode-go\n", "pool needs a base_url"),
        ("    api_key_file: ~/k\n", "api_key_file needs a base_url"),
        ("    env: {FOO: bar}\n", "env needs a base_url"),
        ("    base_url: https://x.y/a\n    pool: none\n"
         "    api_key_file: ${HOME}/k\n", "api_key_file takes a path"),
        ("    base_url: https://x.y/a\n    pool: none\n    env: [a]\n",
         "env must map variable names to strings"),
        ("    base_url: https://x.y/a\n    pool: none\n    env: {N: 3}\n",
         "env must map variable names to strings"),
    ],
)  # fmt: skip
def test_a_connection_is_refused_with_the_agents_error(tmp_path, extra, fragment):
    (a,) = load_agents(write(tmp_path, "agents:\n" + GO + extra))
    assert a.error and fragment in a.error, a.error
    assert not a.enabled
    assert (a.base_url, a.pool, a.env) == ("", "", {})


def test_base_url_is_refused_on_another_harness(tmp_path):
    (a,) = load_agents(
        write(tmp_path, "agents:\n  oc: {harness: opencode, model: opencode-go/x, "
              "effort: high, permission: full, base_url: 'https://x.y/a', "
              "pool: opencode-go}\n")
    )  # fmt: skip
    assert a.error == "base_url is for claude-code agents"


@pytest.mark.parametrize(
    "name",
    [
        "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
        "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR",
        "HOME", "GITHUB_TOKEN", "OPENAI_API_KEY", "MY_SECRET",
    ],
)  # fmt: skip
def test_env_refuses_a_credential_or_the_config_directory(tmp_path, name):
    (a,) = load_agents(
        write(tmp_path, "agents:\n" + GO + "    base_url: https://x.y/a\n"
              f"    pool: none\n    env: {{{name}: x}}\n")
    )  # fmt: skip
    assert a.error and f"env must not set {name}" in a.error


def test_a_connection_inside_provider_is_refused(tmp_path):
    (a,) = load_agents(
        write(
            tmp_path,
            "agents:\n  nested:\n"
            "    provider: {name: claude-code, model: m, effort: high, "
            "permission: full, base_url: 'https://x.y/a', pool: none}\n",
        )
    )
    assert a.error and "base_url, pool go on the agent, not in provider:" in a.error
    assert a.base_url == ""


def go_agents(tmp_path):
    return load_agents(
        write(
            tmp_path,
            "agents:\n  go:\n    harness: claude-code\n    effort: high\n"
            "    permission: full\n    models: [deepseek-v4.1-flash, kimi-k2]\n"
            "    base_url: https://opencode.ai/inference/go/anthropic\n"
            "    pool: opencode-go\n    api_key_file: /keys/go.key\n"
            "    env: {ANTHROPIC_DEFAULT_HAIKU_MODEL: deepseek-v4.1-flash}\n",
        )
    )


def test_resolve_copies_the_connection_and_its_pool(tmp_path):
    spec = resolve(go_agents(tmp_path), None, "go", {}, tmp_path)
    assert spec.pool == "opencode-go"
    assert spec.connection == Connection(
        base_url="https://opencode.ai/inference/go/anthropic",
        api_key_file="/keys/go.key",
        env=(("ANTHROPIC_DEFAULT_HAIKU_MODEL", "deepseek-v4.1-flash"),),
    )
    assert spec.cascade == ("deepseek-v4.1-flash", "kimi-k2")
    hash(spec.connection)  # pairs, not a dict: the connection is hashable


def test_a_model_override_keeps_the_connection(tmp_path):
    spec = resolve(go_agents(tmp_path), None, "go", {"model": "kimi-k2"}, tmp_path)
    assert spec.model == "kimi-k2" and spec.cascade == ()
    assert spec.pool == "opencode-go"
    assert spec.connection and spec.connection.base_url.startswith("https://opencode")


def test_a_harness_override_drops_the_connection_and_derives_the_pool(tmp_path):
    spec = resolve(
        go_agents(tmp_path),
        None,
        "go",
        {"harness": "opencode", "model": "opencode-go/kimi-k2"},
        tmp_path,
    )
    assert (spec.connection, spec.pool) == (None, None)
    assert pool_of(spec) == "opencode-go"  # derived from harness and model


def test_an_agent_without_a_connection_resolves_none(agents, tmp_path):
    spec = resolve(agents, None, "opus", {}, tmp_path)
    assert (spec.connection, spec.pool) == (None, None)
