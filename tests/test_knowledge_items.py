from datetime import date
from hashlib import sha256
from subprocess import run

import pytest

from aegis.knowledge.items import (
    Item,
    check_name,
    dump,
    dump_memory,
    parse_memory,
    parse_skill,
    read_item,
)
from aegis.knowledge import history
from aegis.ops import OpError

MEM = """---
name: deploy-destination
description: Check where a service runs before touching a server
type: feedback
lifetime: temporary
expires: 2026-12-01
paths: [repos/bot/*]
---
Body with [[other-item]].
"""


@pytest.mark.parametrize(
    "bad", ["../x", "Deploy Notes", "", "-lead", "a" * 65, "UPPER"]
)
def test_unsafe_names_are_refused(bad):
    with pytest.raises(OpError) as e:
        check_name(bad)
    assert e.value.code == "bad_name"


def test_a_memory_round_trips():
    it = parse_memory(MEM, "deploy-destination")
    assert it.kind == "memory" and it.status == "approved"
    assert it.lifetime == "temporary" and it.expires == date(2026, 12, 1)
    assert it.paths == ["repos/bot/*"] and "[[other-item]]" in it.body
    assert parse_memory(dump_memory(it), it.name) == it


def test_a_skill_keeps_its_aegis_block():
    it = parse_skill(
        "---\nname: release\ndescription: Cut a release\naegis:\n  scripts: [bump.py]\n---\n1. go\n",
        "release",
    )
    assert it.kind == "skill" and it.meta["aegis"]["scripts"] == ["bump.py"]


def test_broken_frontmatter_raises_value_error():
    with pytest.raises(ValueError):
        parse_memory("---\nname: [unclosed\n---\nx", "x")


def test_a_file_without_frontmatter_raises_value_error():
    with pytest.raises(ValueError):
        parse_memory("just text\n", "x")


def test_a_dash_run_inside_a_value_is_not_a_delimiter():
    it = Item("memory", "sep", "use a---b separator", "Body.\n", {"type": "user"})
    back = parse_memory(dump(it), "sep")
    assert back.description == "use a---b separator"
    assert back.meta == {"type": "user"} and back.body == "Body.\n"


def test_a_crlf_memory_loads():
    text = "---\r\nname: crlf\r\ndescription: from windows\r\n---\r\nLine one.\r\n"
    it = parse_memory(text, "crlf")
    assert it.description == "from windows" and it.body == "Line one.\n"


def test_worktrees_have_distinct_ids_and_revisions_match_the_bytes_read(tmp_path):
    root = tmp_path / "checkout"
    other = tmp_path / "worktree"
    rel = "release/SKILL.md"
    path = root / rel
    path.parent.mkdir(parents=True)
    original = b"---\r\ntitle: Release checklist\r\n---\r\nBuild.\r\n"
    path.write_bytes(original)
    history.ensure_repo(root)
    history.commit(root, [rel], "save skill", "test")
    run(["git", "-C", str(root), "worktree", "add", "--detach", str(other)], check=True)

    first, text = read_item(root, path, parse_skill, "release", scope="workspace")
    second, _ = read_item(other, other / rel, parse_skill, "release", scope="workspace")
    assert first.id != second.id and first.source_id != second.source_id
    assert first.relative_path == second.relative_path == rel
    assert first.revision == second.revision == sha256(original).hexdigest()
    assert text.encode() == original and first.body == "Build.\n"
    assert (first.title, first.format, first.scope) == (
        "Release checklist",
        "markdown",
        "workspace",
    )

    (other / rel).write_bytes(original.replace(b"Build.", b"Test."))
    updated, _ = read_item(
        other, other / rel, parse_skill, "release", scope="workspace"
    )
    assert updated.id == second.id and updated.revision != second.revision
    assert updated.body == "Test.\n"


def test_dump_writes_one_key_per_line():
    it = Item("memory", "m", "d", "B\n", {"type": "user", "paths": ["a/*", "b"]})
    assert dump(it) == (
        "---\nname: m\ndescription: d\ntype: user\npaths:\n- a/*\n- b\n---\nB\n"
    )
