import pytest

from aegis.quota_gate import decide, pool_for, validate

NOW = 10_000.0


def snap(*windows, read_at=NOW - 30, state="ok", name="claude"):
    return {
        "providers": [
            {"name": name, "state": state, "read_at": read_at, "windows": list(windows)}
        ]
    }


def w(kind, percent, projected=None, resets_at=NOW + 3600, label=None):
    return {
        "kind": kind,
        "label": label or kind,
        "percent": percent,
        "projected": projected,
        "resets_at": resets_at,
    }


LINES = {
    "claude": {
        "mode": "lines",
        "lines": {
            "session": {"launch": 50, "stop": 90},
            "weekly_all": {"launch": "pace", "stop": 90},
        },
    }
}


def test_the_pool_of_a_harness():
    assert pool_for("claude-code", "opus") == "claude"
    assert pool_for("opencode", "opencode-go/kimi") == "opencode-go"
    assert pool_for("opencode", "anthropic/sonnet") is None
    assert pool_for("codex", "gpt") == "codex"


@pytest.mark.parametrize(
    "windows,state",
    [
        ((w("session", 40), w("weekly_all", 30, projected=80)), "open"),
        ((w("session", 50), w("weekly_all", 30, projected=80)), "closed"),
        ((w("session", 40), w("weekly_all", 30, projected=120)), "closed"),
        ((w("session", 90), w("weekly_all", 30, projected=80)), "stopped"),
        ((w("session", 40), w("weekly_all", 95, projected=99)), "stopped"),
    ],
)
def test_lines_decide(windows, state):
    assert decide(LINES, "claude", snap(*windows), NOW).state == state


def test_no_policy_is_never_gated():
    assert decide(None, "claude", snap(w("session", 99)), NOW).state == "open"
    assert decide(None, None, snap(), NOW).state == "open"


def test_a_pool_the_policy_does_not_name_is_closed():
    assert decide(LINES, "opencode-go", snap(w("session", 1)), NOW).state == "closed"
    assert decide(LINES, None, snap(w("session", 1)), NOW).state == "closed"
    assert decide(LINES, "codex", snap(), NOW).state == "closed"


def test_codex_has_no_gauge_and_opens_only_when_named():
    assert decide({"codex": {"mode": "open"}}, "codex", snap(), NOW).state == "open"
    assert decide({"codex": {"mode": "closed"}}, "codex", snap(), NOW).state == "closed"


def test_open_and_closed_modes():
    opened = {"claude": {"mode": "open"}}
    assert decide(opened, "claude", snap(w("session", 99)), NOW).state == "open"
    shut = {"claude": {"mode": "closed"}}
    assert decide(shut, "claude", snap(w("session", 0)), NOW).state == "closed"


def test_a_stale_reading_closes_but_never_stops():
    old = snap(w("session", 99), read_at=NOW - 901, state="stale")
    assert decide(LINES, "claude", old, NOW).state == "closed"
    recent = snap(w("session", 99), read_at=NOW - 600, state="stale")
    assert decide(LINES, "claude", recent, NOW).state == "stopped"
    calm = snap(w("session", 10), read_at=NOW - 600, state="stale")
    assert decide(LINES, "claude", calm, NOW).state == "open"


def test_no_reading_at_all_is_closed():
    assert decide(LINES, "claude", {"providers": []}, NOW).state == "closed"
    failed = snap(read_at=None, state="failed")
    assert decide(LINES, "claude", failed, NOW).state == "closed"


def test_the_why_names_the_window_and_the_line():
    g = decide(
        LINES,
        "claude",
        snap(w("session", 91, label="5 hours"), w("weekly_all", 10, projected=50)),
        NOW,
    )
    assert "5 hours" in g.why and "91" in g.why and "90" in g.why
    assert g.resets_at == NOW + 3600


@pytest.mark.parametrize(
    "policy,ok",
    [
        (LINES, True),
        ({}, True),
        ({"claude": {"mode": "open"}}, True),
        ({"codex": {"mode": "open"}}, True),
        ({"codex": {"mode": "lines", "lines": {}}}, False),
        ({"claude": {"mode": "lines"}}, False),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": 95, "stop": 90}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": "pace", "stop": 101}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"session": {"launch": True, "stop": 90}},
                }
            },
            False,
        ),
        (
            {
                "claude": {
                    "mode": "lines",
                    "lines": {"rolling": {"launch": 5, "stop": 90}},
                }
            },
            False,
        ),
        ({"claude": {"mode": "maybe"}}, False),
        ({"nope": {"mode": "open"}}, False),
        ([], False),
    ],
)
def test_validate(policy, ok):
    assert (validate(policy) == []) is ok


def test_a_hand_edited_pool_that_does_not_validate_is_closed():
    broken = {"claude": {"mode": "lines", "lines": {"session": {"launch": 50}}}}
    g = decide(broken, "claude", snap(w("session", 10)), NOW)
    assert g.state == "closed" and "stop is 1 to 100" in g.why


def test_a_policy_in_one_line():
    from aegis.quota_gate import describe

    assert describe(None) == "never gated"
    assert describe({}) == "every pool closed"
    assert describe(LINES | {"codex": {"mode": "open"}}) == (
        "claude: session 50/90, weekly_all pace/90; codex: open"
    )


def test_a_refusal_names_the_line_that_reopens_the_gate():
    from aegis.quota_gate import refusal

    stopped = decide(LINES, "claude", snap(w("session", 95, label="5 hours")), NOW)
    text = refusal("ben", stopped)
    assert "stop line 90%" in text and "drops below 50%" in text and "resets at" in text
    paced = decide(LINES, "claude", snap(w("weekly_all", 30, projected=130)), NOW)
    assert "back on pace" in refusal("ben", paced)


def test_the_three_line_forms_read_as_one_map():
    from aegis.quota_gate import line_of

    assert line_of(80) == {"used": 80}
    assert line_of("pace") == {"projected": 100}
    assert line_of({"used": 80, "projected": 90, "stop": 97}) == {
        "used": 80,
        "projected": 90,
        "stop": 97,
    }
    assert line_of(True) is None and line_of("soon") is None


GUARD = {"claude": {"weekly_all": {"used": 80, "projected": 90, "stop": 97}}}


@pytest.mark.parametrize(
    "percent,projected,state,says",
    [
        (60, 85, "open", ""),
        (86, 85, "closed", "at 86%, launch line 80%"),
        (60, 104, "closed", "on pace for 104% at reset, launch line 90%"),
        (97, 120, "stopped", "stop line 97%"),
    ],
)
def test_a_map_line_closes_on_used_or_projected_and_stops_on_used(
    percent, projected, state, says
):
    from aegis.quota_gate import lines_gate

    g = lines_gate(GUARD, "claude", snap(w("weekly_all", percent, projected)), NOW)
    assert g.state == state and says in g.why
    if state != "open":
        assert g.reopens == "drops below 80% used and 90% projected"


def test_a_map_line_has_no_line_on_a_pool_never_read_and_a_stale_one_closes():
    from aegis.quota_gate import lines_gate

    assert lines_gate(GUARD, "claude", {"providers": []}, NOW).state == "open"
    assert lines_gate(GUARD, "opencode-go", snap(), NOW).state == "open"
    old = snap(w("weekly_all", 99, 120), read_at=NOW - 901)
    assert lines_gate(GUARD, "claude", old, NOW).state == "closed"


@pytest.mark.parametrize(
    "quota,says",
    [
        (GUARD, None),
        ({"claude": {"session": 50, "weekly_all": "pace"}}, None),
        ({"claude": {"weekly_all": {"stop": 90}}}, "names used, projected or both"),
        ({"claude": {"weekly_all": {"used": 95, "stop": 90}}}, "used is below stop"),
        ({"claude": {"weekly_all": {"used": 80, "soon": 1}}}, "not soon"),
        ({"claude": {"weekly_all": True}}, "a line is a percent"),
        ({"claude": {"rolling": 50}}, "has no window"),
        ({"nope": {"weekly": 50}}, "reads no quota"),
    ],
)
def test_quota_errors(quota, says):
    from aegis.quota_gate import quota_errors

    errs = quota_errors(quota)
    assert (says is None and errs == []) or any(says in e for e in errs)
