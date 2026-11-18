import pytest

from aegis.signin.recipes import load_recipes, parse_recipes
from aegis.signin.screen import Screen
from tests.test_signin_screen import RECORDED_STATE, recording


def screen_of(name: str, upto: str | None = None) -> str:
    s = Screen(120, 40)
    for chunk in recording(name, upto):
        s.feed(chunk)
    return s.text()


def test_the_paste_prompt_matches_link_with_the_url():
    rule, found = load_recipes()["claude-code"].match(
        screen_of("claude/setup-token.jsonl", "Paste")
    )
    assert (rule.name, rule.step) == ("link", "paste")
    assert found["url"].endswith("state=" + RECORDED_STATE)


def test_the_end_matches_token():
    rule, found = load_recipes()["claude-code"].match(
        screen_of("claude/setup-token.jsonl")
    )
    assert (rule.name, rule.step) == ("token", "checking")
    assert found["token"].startswith("sk-ant-oat01-FAKE") and len(found["token"]) == 108


def test_a_wrong_code_matches_error():
    rule, _ = load_recipes()["claude-code"].match(
        screen_of("claude/setup-token-bad-code.jsonl")
    )
    assert (rule.name, rule.step) == ("error", "failed")


def test_an_unknown_key_is_named():
    with pytest.raises(ValueError, match="'colz'"):
        parse_recipes('[x]\ncmd = ["x"]\ncolz = 3\n')


def oc_screen(name: str, upto: str | None = None) -> str:
    s = Screen(600, 40)
    for chunk in recording(name, upto):
        s.feed(chunk)
    return s.text()


def test_the_opencode_menu_offers_go_and_console():
    rule, found = load_recipes()["opencode"].match(
        oc_screen("opencode/go.jsonl", "Select integration")
    )
    assert (rule.name, rule.step) == ("integration", "choose")
    assert found["options"] == ["OpenCode Go", "OpenCode Console"]
    assert found["rows"][found["current"]] == "OpenCode Go"


def test_opencode_go_asks_for_its_key_then_connects():
    r = load_recipes()["opencode"]
    rule, found = r.match(
        oc_screen("opencode/go.jsonl", "Enter your OpenCode Go API key")
    )
    assert (rule.name, rule.step, rule.prompt) == (
        "key",
        "paste",
        "Paste your OpenCode Go API key.",
    )
    rule, _ = r.match(oc_screen("opencode/go.jsonl"))
    assert (rule.name, rule.step) == ("connected", "checking")


def test_opencode_console_picks_a_method_then_shows_a_device_code():
    r = load_recipes()["opencode"]
    rule, found = r.match(
        oc_screen("opencode/console.jsonl", "API key (service account)")
    )
    assert (rule.name, rule.step) == ("method", "choose")
    assert found["options"] == ["OpenCode Console account", "API key (service account)"]
    rule, found = r.match(oc_screen("opencode/console.jsonl"))
    assert (rule.name, rule.step) == ("device", "device_code")
    assert found["code"] == "ABCD-EFGH"
    assert found["url"].startswith(
        "https://opencode.ai/console/device?user_code=ABCD-EFGH"
    )
