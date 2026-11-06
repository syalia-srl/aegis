from aegis.signin.mask import DEFAULT_SHAPES, Masker


def test_a_token_split_across_bursts_is_masked():
    m = Masker(DEFAULT_SHAPES)
    tok = "sk-ant-oat01-" + "a" * 90
    out = m.feed("Your token: " + tok[:30]) + m.feed(tok[30:] + "\r\n") + m.flush()
    assert tok[:20] not in out and "••••" in out
    assert out.startswith("Your token: ") and out.endswith("\r\n")


def test_text_without_a_token_passes_unchanged_and_in_order():
    m = Masker(DEFAULT_SHAPES)
    parts = ["Paste code here", " if prompted > ", "\x1b[2K done\r\n"]
    assert "".join(m.feed(p) for p in parts) + m.flush() == "".join(parts)


def test_a_lone_sk_is_held_then_released():
    m = Masker(DEFAULT_SHAPES)
    assert m.feed("ask") == "a"
    assert m.feed("ing nicely") == "sking nicely"


def test_a_token_ended_by_an_escape_is_masked():
    m = Masker(DEFAULT_SHAPES)
    tok = "sk-ant-oat01-" + "b" * 95
    out = m.feed(tok + "\x1b[1CStore") + m.flush()
    assert "bbbbbbbbbbbb" not in out and out == "••••\x1b[1CStore"


def test_a_split_token_with_an_s_inside_is_masked():
    m = Masker(DEFAULT_SHAPES)
    tok = "sk-ant-oat01-" + "aSs9s_" * 15
    out = m.feed("> " + tok[:40]) + m.feed(tok[40:] + "\r\n") + m.flush()
    assert tok[:20] not in out and out == "> ••••\r\n"
