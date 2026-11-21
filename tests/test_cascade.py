import time

import pytest

from aegis.cascade import DEFAULT_COOL_S, Cooling, Fall, classify, cooling_owner, pick
from aegis.quota_gate import NONE, Gate

# The verdict task 1 recorded in cascade.py's docstring.
SCOPE_ZEN = "model"

CASES = [
    ("opencode", "APIError: 429 FreeUsageLimitError: free usage limit reached", Fall(SCOPE_ZEN, None)),
    ("opencode", "APIError: Rate limit exceeded, retry after 30s", Fall("model", 30.0)),
    ("opencode", "APIError: 503 Service Unavailable", Fall("model", None)),
    ("opencode", "APIError: overloaded", Fall("model", None)),
    ("opencode", "ProviderModelNotFoundError: model not found", Fall("model", None)),
    ("claude-code", "Claude AI usage limit reached|1792400000", Fall("pool", None)),
    ("claude-code", "API Error: 529 Overloaded", Fall("model", None)),
    # Claude Code's words for an API 429 (the canary): the model, not the pool.
    ("claude-code", "API Error: Request rejected (429) · rate limit reached", Fall("model", None)),
    ("opencode", "aborted", None),
    ("opencode", "interrupted", None),
    ("opencode", "ContextOverflowError: prompt is too long", None),
    ("opencode", "ProviderAuthError: invalid api key", None),
    (
        "opencode",
        "provider.auth: OpenCode's free models aren't available from your current network"
        " because we've detected abusive traffic from this IP address.",
        None,
    ),
    ("opencode", "something nobody has seen", None),
    # An Anthropic API 429 names the maximum tokens; it is a rate limit.
    (
        "claude-code",
        'API Error: 429 {"type":"error","error":{"type":"rate_limit_error","message":'
        '"This request would exceed the rate limit for your organization of 30,000'
        " input tokens per minute. Please reduce the prompt length or the maximum"
        ' tokens requested, or try again later."}}',
        Fall("model", None),
    ),
    # A gateway timeout and a fetch timeout fall; they are not context or abort.
    ("opencode", "UnknownError: context deadline exceeded", Fall("model", None)),
    ("opencode", "TimeoutError: The operation was aborted due to timeout", Fall("model", None)),
    ("opencode", "APIError: model opencode/x not found", Fall("model", None)),
    # 429 inside an id, and a not-found that names no model, stay.
    ("opencode", "APIError: tool_use id toolu_01Xk4290 has no result", None),
    ("opencode", "NotFoundError: Session not found", None),
    ("opencode", "AbortError: The operation was aborted", None),
]  # fmt: skip

T = 1_800_000_000.0


def open_gate(model: str) -> Gate:
    return Gate("open")


@pytest.mark.parametrize("harness,text,want", CASES)
def test_classify(harness, text, want):
    assert classify(harness, text) == want


def test_a_codex_error_never_falls():
    assert classify("codex", "429 rate limit") is None


def test_claude_usage_limit_keeps_its_reset_epoch():
    fall = classify("claude-code", "Claude AI usage limit reached|1792400000")
    assert fall is not None and fall.until == 1792400000.0
    c = Cooling()
    assert c.cool("claude-code", "opus", fall, 1792390000.0) == 1792400000.0


def test_pick_skips_cooling_and_gated_models():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", None), T)

    def gate(model: str) -> Gate:
        return Gate("closed", "no") if model == "opencode/b" else Gate("open")

    models = ["opencode/a", "opencode/b", "opencode/c"]
    assert pick(models, -1, "opencode", gate, c, T) == ("opencode/c", "")


def test_pick_after_never_wraps():
    model, why = pick(
        ["opencode/a", "opencode/b"], 1, "opencode", open_gate, Cooling(), T
    )
    assert model is None and why.startswith("every model in the cascade")


def test_a_pool_fall_cools_every_model_in_the_pool():
    c = Cooling()
    c.cool("claude-code", "opus", Fall("pool", None), T)
    assert c.until("claude-code", "sonnet", T) == T + DEFAULT_COOL_S
    assert c.until("opencode", "opencode/a", T) is None


def test_an_opencode_pool_fall_cools_its_provider_prefix():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("pool", None), T)
    assert c.until("opencode", "opencode/b", T) == T + DEFAULT_COOL_S
    assert c.until("opencode", "opencode-go/x", T) is None


def test_retry_after_sets_the_cooling_end():
    c = Cooling()
    assert c.cool("opencode", "opencode/a", Fall("model", 30.0), T) == T + 30
    assert c.until("opencode", "opencode/a", T) == T + 30
    assert c.until("opencode", "opencode/b", T) is None


def test_cooling_expires():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", 30.0), T)
    assert c.until("opencode", "opencode/a", T + 31) is None
    assert c.earliest(T + 31) is None and c.wire(T + 31) == []
    assert pick(["opencode/a"], -1, "opencode", open_gate, c, T + 31)[0] == "opencode/a"


def test_why_names_the_earliest_free_time():
    c = Cooling()
    c.cool("opencode", "opencode/a", Fall("model", 600.0), T)
    c.cool("opencode", "opencode/b", Fall("model", 60.0), T)
    model, why = pick(["opencode/a", "opencode/b"], -1, "opencode", open_gate, c, T)
    clock = time.strftime("%H:%M", time.localtime(T + 60))
    assert model is None
    assert (
        why
        == f"every model in the cascade is cooling or gated; the first is free again at {clock}"
    )
    assert c.earliest(T) == T + 60
    assert c.wire(T) == [
        {"harness": "opencode", "model": "opencode/a", "until": T + 600},
        {"harness": "opencode", "model": "opencode/b", "until": T + 60},
    ]


GO = "https://opencode.ai/inference/go/anthropic"
GATEWAY = "https://gateway.example/anthropic"


def test_the_cooling_owner_is_the_endpoint_else_the_harness():
    assert cooling_owner("claude-code", None) == "claude-code"
    assert cooling_owner("claude-code", GO) == GO


def test_a_pool_fall_on_another_pool_cools_that_pool_and_not_claude():
    """A claude-code session drawing on opencode-go: a Go limit must not
    cool Opus."""
    c = Cooling()
    c.cool("claude-code", "deepseek-v4.1-flash", Fall("pool", None), T, "opencode-go")
    assert c.until("claude-code", "glm-5", T, "opencode-go") == T + DEFAULT_COOL_S
    assert (
        c.until("opencode", "opencode-go/kimi", T, "opencode-go") == T + DEFAULT_COOL_S
    )
    assert c.until("claude-code", "opus", T, "claude") is None
    assert c.until("claude-code", "opus", T) is None
    assert c._until == {("opencode-go", "pool", "opencode-go"): T + DEFAULT_COOL_S}


def test_a_claude_pool_fall_is_keyed_by_the_pool_alone():
    c = Cooling()
    c.cool("claude-code", "opus", Fall("pool", None), T, "claude")
    assert c._until == {("claude", "pool", "claude"): T + DEFAULT_COOL_S}
    # A caller that names no pool derives it as before (pool_for).
    assert c.until("claude-code", "sonnet", T) == T + DEFAULT_COOL_S


def test_two_unmetered_gateways_never_share_a_pool_fall():
    c = Cooling()
    c.cool(GO, "m", Fall("pool", None), T, None)
    assert c._until == {(GO, "pool", GO): T + DEFAULT_COOL_S}
    assert c.until(GO, "other", T, None) == T + DEFAULT_COOL_S
    assert c.until(GATEWAY, "m", T, None) is None


def test_a_connection_model_fall_is_keyed_by_its_endpoint():
    """The key cool writes is the key pick reads: the same connection skips
    the model, another endpoint and the bare harness do not."""
    c = Cooling()
    c.cool(cooling_owner("claude-code", GO), "m", Fall("model", None), T, "opencode-go")
    assert c._until == {(GO, "model", "m"): T + DEFAULT_COOL_S}
    go = lambda m: "opencode-go"  # noqa: E731
    assert pick(["m", "n"], -1, GO, open_gate, c, T, go) == ("n", "")
    assert pick(["m", "n"], -1, GATEWAY, open_gate, c, T, lambda m: None) == ("m", "")
    assert pick(["m"], -1, "claude-code", open_gate, c, T, lambda m: "claude") == (
        "m",
        "",
    )


def test_pick_asks_the_pool_of_each_candidate():
    c = Cooling()
    c.cool("opencode", "opencode-go/a", Fall("pool", None), T, "opencode-go")
    pools = {"opencode-go/a": "opencode-go", "opencode/b": None}
    seen: list[str] = []

    def pool(m: str) -> str | None:
        seen.append(m)
        return pools[m]

    assert pick(
        ["opencode-go/a", "opencode/b"], -1, "opencode", open_gate, c, T, pool
    ) == (
        "opencode/b",
        "",
    )
    assert seen == ["opencode-go/a", "opencode/b"]


def test_the_wire_names_the_pool_of_a_pool_fall():
    c = Cooling()
    c.cool("claude-code", "m", Fall("pool", 60.0), T, "opencode-go")
    c.cool(GO, "m", Fall("model", 30.0), T, "opencode-go")
    assert c.wire(T) == [
        {"endpoint": GO, "model": "m", "until": T + 30},
        {"pool": "opencode-go", "until": T + 60},
    ]


def test_a_catalogue_row_reads_the_keys_its_agents_draw_on():
    """A model used only by a connection agent reports that endpoint's fall;
    a model used by both keeps the harness fall; a free agent never hides
    another's fall."""
    c = Cooling()
    c.cool(GO, "glm-5", Fall("model", 60.0), T, "opencode-go")
    c.cool("claude-code", "opus", Fall("model", 30.0), T, "claude")
    go = (GO, "opencode-go")
    claude = ("claude-code", "claude")
    assert c.latest("claude-code", "glm-5", T, [go]) == T + 60
    assert c.latest("claude-code", "glm-5", T, []) is None
    assert c.latest("claude-code", "opus", T, [go, claude]) == T + 30
    assert c.latest("claude-code", "opus", T, [go]) == T + 30
    assert c.latest("claude-code", "opus", T + 31, [go, claude]) is None


def test_a_catalogue_row_shows_a_pool_fall_of_its_agents_pool():
    c = Cooling()
    c.cool(GO, "glm-5", Fall("pool", 120.0), T, "opencode-go")
    assert c.latest("claude-code", "kimi-k3", T, [(GO, "opencode-go")]) == T + 120
    assert c.latest("claude-code", "kimi-k3", T, [("claude-code", "claude")]) is None


def test_a_harness_pool_fall_on_a_prefix_stays_in_its_harness():
    """An opencode model named ``claude/x`` derives no pool (pool_for): its
    pool fall keys the prefix under opencode, not Claude Code's ``claude``."""
    c = Cooling()
    c.cool("opencode", "claude/x", Fall("pool", None), T)
    assert c._until == {("opencode", "pool", "claude"): T + DEFAULT_COOL_S}
    assert c.until("claude-code", "opus", T, "claude") is None
    assert c.until("claude-code", "opus", T) is None
    assert c.until("opencode", "claude/y", T) == T + DEFAULT_COOL_S
    assert c.until("pi", "claude/x", T) is None


@pytest.mark.parametrize(
    "harness, model", [("claude-code", "opus"), ("opencode", "claude/x")]
)
def test_a_pool_fall_with_pool_none_cools_the_model_alone(harness, model):
    """A recorded ``pool: none`` has no pool: its pool fall cools that model
    and never the pool its harness would derive."""
    c = Cooling()
    c.cool(harness, model, Fall("pool", None), T, NONE)
    assert c._until == {(harness, "model", model): T + DEFAULT_COOL_S}
    assert c.until("claude-code", "sonnet", T, "claude") is None
    assert c.until(harness, model, T, NONE) == T + DEFAULT_COOL_S
    assert c.until(harness, "other", T, NONE) is None


def test_an_endpoint_with_pool_none_keys_its_pool_fall_by_itself():
    c = Cooling()
    c.cool(GO, "m", Fall("pool", None), T, NONE)
    assert c._until == {(GO, "pool", GO): T + DEFAULT_COOL_S}
    assert c.until(GO, "other", T, NONE) == T + DEFAULT_COOL_S


def test_a_catalogue_row_sees_a_fall_written_under_its_resolved_id():
    c = Cooling()
    c.cool("claude-code", "claude-opus-5-5", Fall("model", 60.0), T, "claude")
    claude = [("claude-code", "claude")]
    assert c.latest("claude-code", "opus", T, claude) is None
    assert c.latest("claude-code", ["opus", "claude-opus-5-5"], T, claude) == T + 60
    assert c.latest("claude-code", ["opus", "opus"], T, claude) is None
