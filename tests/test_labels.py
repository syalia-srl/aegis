"""The label tables in English and Spanish (plan 2026-11-08, slice a5).

Every string the client shows is a key in a table; ``en`` and ``es`` ship with
aegis. The tables are plain ES modules that must stay JSON-compatible, which is
what lets this test read them with ``json.loads`` and hold both sides to the
same keys and the same ``{name}`` variables: a key added to one language and
not the other, or a variable renamed in one, is a page that shows a key name or
a sentence with a hole in it.

The last test guards the other half of the slice: the transcript's chrome and
rows must take their words from the table, not keep a copy in the module that
the next translation would miss.
"""

import json
import re
from pathlib import Path

LABELS = Path(__file__).parents[1] / "src/aegis/client/js/labels"
CLIENT = Path(__file__).parents[1] / "src/aegis/client/js"

VARIABLE = re.compile(r"\{(\w+)\}")


def table(lang: str) -> dict[str, str]:
    """One table, from the module's ``export default`` to the closing brace.

    The files carry a comment above the object, so the marker is what starts
    the JSON, and the module's trailing semicolon is not JSON.
    """
    src = (LABELS / f"{lang}.js").read_text(encoding="utf-8")
    body = src[src.index("export default") + len("export default") :].strip()
    assert body.endswith(";"), f"{lang}.js must end its export with a semicolon"
    return json.loads(body[:-1])


def test_en_and_es_hold_the_same_keys():
    en, es = table("en"), table("es")
    assert set(en) == set(es), (
        f"only in en: {sorted(set(en) - set(es))}; only in es: {sorted(set(es) - set(en))}"
    )


def test_every_key_names_its_part_and_a_thing():
    for key in table("en"):
        part, _, thing = key.partition(".")
        assert part and thing, f"{key} is not <part>.<thing>"
        assert re.fullmatch(r"[a-z0-9]+(\.[A-Za-z0-9]+)+", key), key


def test_both_languages_use_the_same_variables():
    en, es = table("en"), table("es")
    for key in en:
        assert set(VARIABLE.findall(en[key])) == set(VARIABLE.findall(es[key])), key


def test_the_transcript_and_its_rows_are_labelled():
    """What a5 moved: the chrome, the rows, find and copy."""
    keys = set(table("en"))
    for prefix in ("transcript.", "entries.", "find.", "copy."):
        assert any(k.startswith(prefix) for k in keys), prefix
    for key in (
        "transcript.offline",
        "transcript.working.escape",
        "transcript.fold.0",
        "transcript.run.toolCall",
        "transcript.nav.message",
        "entries.file.download",
        "entries.tool.steps",
        "entries.btw.cancel",
        "find.placeholder",
        "find.none",
        "copy.copied",
    ):
        assert key in keys, key


def test_no_transcript_literal_was_left_in_the_modules():
    """The words a5 moved live in the table only.

    Each row names a literal the module used to carry, the key that replaced
    it, and the modules that must not hold it any more: a copy left behind
    would show English to a Spanish mount and nobody would translate it. The
    modules are named one by one because a literal can be legitimately owned
    by a phase-B card in the same file (entries.js's secret card still says
    "Copied" until b9 moves the remaining cards).
    """
    en = table("en")
    moved = {
        "transcript.offline": ("Not saved on this device", ["parts/transcript.js"]),
        "transcript.cold.continue": (
            "Continue in a fresh session",
            ["parts/transcript.js"],
        ),
        "transcript.working.escape": ("Esc to interrupt", ["parts/transcript.js"]),
        "transcript.nav.recap": (
            "Recap where this session stands",
            ["parts/transcript.js"],
        ),
        "transcript.run.show": ("Show these steps", ["transcript.js"]),
        "transcript.needsConnection": ("Needs the connection.", ["transcript.js"]),
        "find.placeholder": ("Find in transcript", ["find.js"]),
        "find.none": ("no matches", ["find.js"]),
        "copy.copied": ("Copied", ["copy.js"]),
        "entries.tool.peekAgain": ("Show the file again", ["entries.js"]),
        "entries.file.download": ("Download", ["entries.js"]),
        "entries.btw.reading": ("reading the transcript…", ["entries.js"]),
    }
    for key, (literal, names) in moved.items():
        assert literal in en[key], (key, literal)
        for name in names:
            src = (CLIENT / name).read_text(encoding="utf-8")
            assert literal not in src, f"{literal} is still in {name}"
