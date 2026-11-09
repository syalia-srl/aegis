"""The label tables in English and Spanish (plan 2026-11-08, slice a5).

Every string the client shows is a key in a table; ``en`` and ``es`` ship with
aegis. The tables are plain ES modules that must stay JSON-compatible, which is
what lets this test read them with ``json.loads`` and hold both sides to the
same keys and the same ``{name}`` variables: a key added to one language and
not the other, or a variable renamed in one, is a page that shows a key name or
a sentence with a hole in it.

The last tests guard the other half of the work: the transcript's and the
composer's chrome and rows must take their words from the table, not keep a
copy in the module that the next translation would miss.
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


def test_every_literal_key_the_client_uses_exists():
    """A key spelled in a module (or a ``data-t`` attribute) is in the table.

    The browser logs a missing key once and shows the key itself, so a typo is
    a page that reads "transcript.oflline" and nobody notices. Keys built at
    run time (a kind, a fold level, a copy noun) are checked by the browser
    tests that draw them.
    """
    en = table("en")
    used = set()
    for path in CLIENT.rglob("*.js"):
        if path.parent.name == "labels":
            continue
        src = path.read_text(encoding="utf-8")
        used |= set(re.findall(r'\bt\("([a-z0-9]+\.[A-Za-z0-9.]+)"', src))
        used |= set(
            re.findall(
                r'data-t(?:-title|-aria|-placeholder)?="([a-z0-9]+\.[A-Za-z0-9.]+)"',
                src,
            )
        )
    assert used, "no label keys found: the scan is looking in the wrong place"
    assert used <= set(en), f"not in the table: {sorted(used - set(en))}"


def test_the_composer_and_its_attachments_are_labelled():
    """What a7 moved: the box's placeholders and buttons, the chips, the
    upload sizes, the errors, the untested question and the outbox's toasts."""
    keys = set(table("en"))
    for prefix in ("composer.", "attach.", "outbox."):
        assert any(k.startswith(prefix) for k in keys), prefix
    # The placeholder has one key per state: translating the default must not
    # force the locked or asleep sentence to change with it.
    placeholders = {k for k in keys if k.startswith("composer.placeholder.")}
    assert len(placeholders) >= 4, sorted(placeholders)
    for key in (
        "composer.placeholder.default",
        "composer.placeholder.locked",
        "composer.placeholder.off",
        "composer.placeholder.asleep",
        "composer.attach",
        "composer.sendTitle",
        "composer.interruptTitle",
        "composer.effort",
        "composer.sending",
        "composer.notConfirmed",
        "composer.untested.startOnce",
        "composer.archive.ok",
        "attach.size.bytes",
        "attach.size.kb",
        "attach.size.mb",
        "attach.remove",
        "attach.uploading",
        "outbox.queued.one",
        "outbox.sent.one",
        "outbox.look.other",
        "outbox.notSent",
    ):
        assert key in keys, key


def test_no_composer_literal_was_left_in_the_modules():
    """The words a7 moved live in the table only.

    Same guard as a5's, for the part the chat sends from: a copy left behind
    would keep an English button or error in a Spanish mount.
    """
    en = table("en")
    moved = {
        "composer.placeholder.default": (
            "Message the agent. Enter sends",
            ["parts/composer.js"],
        ),
        "composer.placeholder.off": (
            "nothing can be sent until it is back",
            ["parts/composer.js"],
        ),
        "composer.attach": ("Attach files", ["parts/composer.js"]),
        "composer.sendTitle": ("Send (Enter)", ["parts/composer.js"]),
        "composer.interruptTitle": ("Interrupt (Esc)", ["parts/composer.js"]),
        "composer.sending": (
            "sending when the attachments finish",
            ["parts/composer.js"],
        ),
        "composer.notConfirmed": ("No answer in 5 s", ["parts/composer.js"]),
        "composer.untested.startOnce": ("Start once", ["parts/composer.js"]),
        "composer.archive.ok": ("Archive", ["parts/composer.js"]),
        "attach.size.bytes": ("n} B", ["attach.js"]),
        "attach.remove": ("Remove", ["attach.js"]),
        "attach.uploading": ("attachments are still uploading", ["attach.js"]),
        "outbox.notSent": ("Not sent: the connection went", ["outbox.js"]),
        "outbox.look.one": ("needs a look", ["outbox.js"]),
    }
    for key, (literal, names) in moved.items():
        assert literal in en[key], (key, literal)
        for name in names:
            src = (CLIENT / name).read_text(encoding="utf-8")
            assert literal not in src, f"{literal} is still in {name}"


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


def test_the_parts_are_labelled():
    """What b9 moved: the chrome every part draws, and the words they share.

    A part keeps its markup in English and names a key beside it, so the
    presence of a key prefix is not enough; the literals test below holds the
    other half.
    """
    keys = set(table("en"))
    for prefix in (
        "sidebar.",
        "header.",
        "tabs.",
        "spawn.",
        "fleet.",
        "commands.",
        "dictation.",
        "shell.",
        "state.",
        "gauges.",
        "time.",
        "terminal.",
        "browser.",
    ):
        assert any(k.startswith(prefix) for k in keys), prefix
    for key in (
        "sidebar.session",
        "sidebar.untestedTitle",
        "header.fleet",
        "header.switchPlaceholder",
        "tabs.fold",
        "spawn.agents",
        "spawn.doctorFix.one",
        "fleet.archive",
        "fleet.thTitle",
        "commands.flag.quiet",
        "dictation.held",
        "shell.signIn",
        "state.needsYou",
        "gauges.cpu",
        "time.justNow",
        "terminal.lineMode",
    ):
        assert key in keys, key


def test_the_parts_take_their_words_from_the_table():
    """A part's markup names a key, and the modules keep no copy of the words.

    The same guard as a5's and a7's, for the rest of the client: a literal left
    behind would keep an English heading in a Spanish mount.
    """
    en = table("en")
    moved = {
        "sidebar.renameNote": (
            "Click the title or the handle to rename.",
            ["parts/sidebar.js"],
        ),
        "sidebar.sleepTitle": ("Sleep: end the process", ["parts/sidebar.js"]),
        "header.newTitle": ("New session (Alt+N)", ["parts/header.js"]),
        "header.knowledgeTitle": (
            "Knowledge: skills and memories",
            ["parts/header.js"],
        ),
        "spawn.placeholder": ("What should the agent do?", ["parts/spawn.js"]),
        "spawn.findingServiceDirective": ("Service {unit}", ["parts/spawn.js"]),
        "fleet.installWhy": ("Opens from the home screen", ["parts/fleet.js"]),
        "fleet.filter": ("Filter by title, handle or directory", ["parts/fleet.js"]),
        "fleet.thActive": ("Last active", ["fleet.js"]),
        "fleet.termAgentHolds": ("the agent holds it", ["fleet.js"]),
        "commands.flag.cwd": ("the directory it works in", ["commands.js"]),
        "dictation.held": ("Release to send", ["record.js"]),
        "dictation.title": ("Dictate (Alt+M)", ["parts/dictation.js"]),
        "shell.connecting": ("Connecting…", ["layouts/full.js"]),
        "terminal.lineMode": ("line mode", ["parts/terminal.js"]),
    }
    for key, (literal, names) in moved.items():
        assert literal in en[key], (key, literal)
        for name in names:
            src = (CLIENT / name).read_text(encoding="utf-8")
            assert literal not in src, f"{literal} is still in {name}"


def test_every_part_that_draws_markup_labels_it():
    """Every part's markup names keys rather than carrying the words alone."""
    for name in (
        "parts/sidebar.js",
        "parts/header.js",
        "parts/spawn.js",
        "parts/fleet.js",
        "parts/terminal.js",
    ):
        src = (CLIENT / name).read_text(encoding="utf-8")
        assert "data-t" in src, f"{name} names no label key"
        assert "applyLabels" in src, f"{name} never fills its keys"


def test_no_card_literal_was_left_in_entries():
    """The cards a transcript draws take their words from the table too.

    A chat mount shows the workflow, secret, secret-request, sign-in, config
    and knowledge cards inside its transcript; a host that mounted it in
    Spanish must not get English buttons and sentences inside them. Each row
    names a literal the module used to carry and the key that replaced it.
    """
    en = table("en")
    moved = {
        "entries.approval.waits": "waits for your go",
        "entries.secret.goneRevealed": "Already viewed. It cannot be shown again.",
        "entries.secret.onlyYou": "Only you can reveal this, once.",
        "entries.request.providedLine": "Provided: {bytes} B written to {path}.",
        "entries.login.renewed": "Claude sign-in renewed",
        "entries.login.step2": "Approve access in the browser.",
        "entries.signin.starting": "Starting the {label} login…",
        "entries.config.applied": "Applied. .aegis.yaml now holds this text",
        "entries.knowledge.importAbout": "Moved in from /setup, as you ticked.",
        "entries.knowledge.done.conflict": "It changed again since,",
        "entries.workflow.notFound": "workflow not found",
        "entries.workflow.kindGateYou": "gate · you decide",
        "entries.workflow.waitsOnMain": "waits on the main agent: {parts}",
        "entries.workflow.adds": "without asking. Steps that ran keep their results.",
        "entries.decision.runAgain.one": "Run again: {kept} done step kept",
        "entries.wfState.armed": "waits for your go",
    }
    src = (CLIENT / "entries.js").read_text(encoding="utf-8")
    for key, literal in moved.items():
        assert literal in en[key], (key, literal)
        assert literal not in src, f"{literal} is still in entries.js"
