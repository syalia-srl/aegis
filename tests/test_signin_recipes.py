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
