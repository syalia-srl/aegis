import json
import re
from pathlib import Path

from aegis.signin.screen import Screen

DATA = Path(__file__).parent / "data" / "sign-in"
RECORDED_STATE = "7qLJhZzXqoSM7CILhfEyqrhbKorebAX1trF0H8QJm30"


def recording(name: str, upto: str | None = None) -> list[str]:
    chunks = []
    for line in (DATA / name).read_text().splitlines():
        chunks.append(json.loads(line)["data"])
        if upto and upto in chunks[-1]:
            break
    return chunks


def test_a_wrapped_url_reads_whole():
    s = Screen(120, 40)
    for chunk in recording("claude/setup-token.jsonl", upto="Paste"):
        s.feed(chunk)
    text = s.text()
    url = re.search(r"https://claude\.com/cai/oauth/authorize\?\S+", text).group(0)
    assert url.endswith("state=" + RECORDED_STATE)
    assert "Paste code here if prompted >" in text


def test_rows_are_right_stripped_and_the_token_reads_whole():
    s = Screen(120, 40)
    for chunk in recording("claude/setup-token.jsonl"):
        s.feed(chunk)
    assert all(r == r.rstrip() for r in s.rows())
    assert re.search(r"sk-ant-oat01-FAKEx+\n\n Store this token", s.text())


def test_a_menu_redraw_leaves_only_the_new_rows():
    s = Screen(40, 10)
    s.feed("Pick one:\r\n> Go\r\n  Console\r\n")
    s.feed("\x1b[3A\x1b[2KPick a plan:\r\n\x1b[2K  Go\r\n\x1b[2K> Console\r\n")
    assert s.rows()[:3] == ["Pick a plan:", "  Go", "> Console"]
