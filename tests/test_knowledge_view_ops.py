# ruff: noqa: F811  (pytest fixtures imported from other test modules)
"""The Knowledge view's ops (knowledge_ops.py): a person lists, reads,
edits, restores and removes the items of their own store, an admin's
included, and every change is one commit."""

from __future__ import annotations

from subprocess import run

import pytest

from aegis.app import App
from aegis.knowledge import history
from aegis.knowledge.items import source_id
from aegis.ops import Caller, OpError, Principal
from aegis.roots import make_roots

from .knowledge_samples import mem, skill
from .one_person import BEN, recorded  # noqa: F401
from .policies import Named, Who
from .test_agents import turn, world  # noqa: F401  (fixture)
from .one_person import ADMIN, homes, spawn  # noqa: F401
from .one_person import world as members  # noqa: F401


async def call(world, op: str, caller: Caller = ADMIN, **params):
    return await world.app.registry.call(op, params, caller)


async def refused(world, op: str, caller: Caller = ADMIN, **params) -> OpError:
    with pytest.raises(OpError) as e:
        await call(world, op, caller, **params)
    return e.value


async def seed(world, *items, owner: str | None = None, extra=None) -> None:
    app = world.app
    for it in items:
        await app.knowledge.store.save(owner or app.user, it, "test", extra=extra)


def text_of(world, name: str, owner: str | None = None) -> str:
    app = world.app
    it = app.knowledge.store.get(owner or app.user, name)
    assert it is not None and it.path is not None
    return it.path.read_text()


async def test_list_puts_proposals_first_and_filters_by_kind(world):
    await seed(
        world,
        skill("release", "Cut a release of the package"),
        mem("deploy-host", "Where the bot is deployed"),
        mem("merge-after-green", "Merge only after every check", status="proposed"),
    )
    rows = (await call(world, "knowledge.list"))["items"]
    assert [r["name"] for r in rows] == ["merge-after-green", "release", "deploy-host"]
    r = rows[1]
    assert (r["kind"], r["status"], r["flags"], r["reads"]) == (
        "skill",
        "approved",
        [],
        0,
    )
    assert r["description"] == "Cut a release of the package" and r["last_used"] is None
    names = lambda got: [r["name"] for r in got["items"]]  # noqa: E731
    assert names(await call(world, "knowledge.list", kind="skill")) == ["release"]
    assert names(await call(world, "knowledge.list", kind="memory")) == [
        "merge-after-green",
        "deploy-host",
    ]
    assert names(await call(world, "knowledge.list", kind="flagged")) == [
        "merge-after-green"
    ]
    assert names(await call(world, "knowledge.list", query="deployed bot")) == [
        "deploy-host"
    ]


async def test_list_counts_reads_and_marks_an_expired_memory(world):
    app = world.app
    await seed(
        world,
        mem("old-freeze", "No releases", lifetime="temporary", expires="2020-01-01"),
    )
    app.knowledge.usage.add(app.user, "s", "memory", "old-freeze", "read")
    app.knowledge.usage.flush()
    (r,) = (await call(world, "knowledge.list", kind="flagged"))["items"]
    assert r["flags"] == ["expired"] and r["reads"] == 1 and r["last_used"] > 0


async def test_get_returns_the_whole_file_its_stamp_and_a_skills_files(world):
    await seed(world, skill("release", "Cut a release"), extra={"go.py": b"print(1)"})
    got = await call(world, "knowledge.get", name="release")
    assert got["text"] == text_of(world, "release")
    assert got["text"].startswith("---\nname: release\n")
    assert got["files"] == ["SKILL.md", "go.py"] and len(got["stamp"]) == 64
    assert (await refused(world, "knowledge.get", name="nope")).code == "not_found"
    assert (await refused(world, "knowledge.get", name="../x")).code == "bad_name"


async def test_qualified_reads_keep_names_and_history_in_each_personal_store(members):
    store = members.app.knowledge.store
    owner = members.app.who(ADMIN).name
    member = Caller("user", principal=Principal("wiki-member"))
    await seed(members, mem("release", "Admin procedure"), owner=owner)
    await seed(
        members,
        mem(
            "release",
            "Member freeze",
            status="proposed",
            lifetime="temporary",
            expires="2020-01-01",
        ),
        owner="wiki-member",
    )
    (admin_row,) = (await call(members, "knowledge.list", ADMIN))["items"]
    (member_row,) = (await call(members, "knowledge.list", member))["items"]
    assert admin_row["id"] != member_row["id"]
    assert (
        admin_row["relative_path"]
        == member_row["relative_path"]
        == "memories/release.md"
    )
    assert admin_row["source_id"] != member_row["source_id"]
    for caller, row, description in (
        (ADMIN, admin_row, "Admin procedure"),
        (member, member_row, "Member freeze"),
    ):
        got = await call(members, "knowledge.get", caller, entry_id=row["id"])
        assert got == await call(members, "knowledge.get", caller, name="release")
        assert got["description"] == description
        assert got["stamp"] == got["revision"] == row["revision"]
        assert got["title"] == "release" and got["scope"] == "personal"
    assert member_row["status"] == "proposed" and member_row["flags"] == ["expired"]
    assert member_row["expires"] == "2020-01-01"
    assert (await call(members, "knowledge.history", member, name="release"))["history"]
    assert (
        await refused(members, "knowledge.get", ADMIN, entry_id=member_row["id"])
    ).code == "not_found"
    assert (
        await refused(members, "knowledge.get", member, entry_id=admin_row["id"])
    ).code == "not_found"

    path = store.dir("wiki-member") / member_row["relative_path"]
    path.write_text(path.read_text() + "New detail\n")
    got = await call(members, "knowledge.get", member, entry_id=member_row["id"])
    assert (
        got["body"].endswith("New detail\n")
        and got["revision"] != member_row["revision"]
    )
    assert (await refused(members, "knowledge.get", member)).code == "bad_params"
    assert (
        await refused(
            members, "knowledge.get", member, name="release", entry_id=got["id"]
        )
    ).code == "bad_params"


async def test_one_caller_reads_homonymous_worktree_entries_by_id(tmp_path):
    main = tmp_path / "checkout"
    branch = tmp_path / "worktree"
    rel = "know-how/release.md"
    path = main / rel
    path.parent.mkdir(parents=True)
    text = "---\ntitle: Release checklist\nwhen: during a release\n---\nBuild.\n"
    path.write_text(text)
    history.ensure_repo(main)
    history.commit(main, [rel], "add release know-how", "test")
    run(
        ["git", "-C", str(main), "worktree", "add", "--detach", str(branch)], check=True
    )
    (branch / rel).write_text(text.replace("Build.", "Test."))

    policy = Named(read_only={"reader": {"alice", "bob"}})
    app = App(make_roots(tmp_path, None), policy=policy)
    reader = Caller("user", principal=Principal("reader", Who("reader")))
    contexts = (
        ("20261126-000000-aaaaaa", "alice", main, "Build.\n"),
        ("20261126-000000-bbbbbb", "bob", branch, "Test.\n"),
    )
    op = "knowledge.worktree.read"
    entries = []
    for log_id, owner, root, body in contexts:
        app.sessions.archived[log_id] = {
            "owner": owner,
            "cwd": str(root),
            "archived": True,
        }
        entry = await app.registry.call(
            op, {"log_id": log_id, "relative_path": rel}, reader
        )
        assert entry == await app.registry.call(
            op, {"log_id": log_id, "entry_id": entry["id"]}, reader
        )
        assert entry["name"] == "release" and entry["title"] == "Release checklist"
        assert entry["description"] == "during a release"
        assert entry["relative_path"] == rel and entry["scope"] == "workspace"
        assert entry["body"] == body
        entries.append(entry)
    assert entries[0]["id"] != entries[1]["id"]
    assert entries[0]["revision"] != entries[1]["revision"]

    main_context, _, _, _ = contexts[0]
    branch_context, _, _, _ = contexts[1]
    with pytest.raises(OpError, match="no matching know-how entry"):
        await app.registry.call(
            op, {"log_id": main_context, "entry_id": entries[1]["id"]}, reader
        )

    (main / "know-how" / "escape.md").symlink_to(branch / rel)
    for rel in ("know-how/escape.md", "know-how/../know-how/release.md"):
        with pytest.raises(OpError, match="no matching know-how entry"):
            await app.registry.call(
                op,
                {"log_id": main_context, "entry_id": f"{source_id(main)}:{rel}"},
                reader,
            )
    policy.hidden["reader"] = {"bob"}
    with pytest.raises(OpError) as denied:
        await app.registry.call(
            op, {"log_id": branch_context, "entry_id": entries[1]["id"]}, reader
        )
    assert denied.value.code == "no_session"


async def test_put_saves_the_edit_approved_as_one_commit(world):
    app = world.app
    await seed(world, mem("merge-after-green", "Merge after green", status="proposed"))
    got = await call(world, "knowledge.get", name="merge-after-green")
    text = got["text"].replace("Merge after green", "Merge after every check")
    out = await call(
        world, "knowledge.put", name="merge-after-green", text=text, stamp=got["stamp"]
    )
    assert out["status"] == "approved" and len(out["sha"]) == 40
    it = app.knowledge.store.get(app.user, "merge-after-green")
    assert it.status == "approved" and it.description == "Merge after every check"
    root = app.knowledge.store.dir(app.user)
    top = history.log(root, "memories/merge-after-green.md")[0]
    assert top["message"] == "edit memory merge-after-green" and top["by"] == app.user
    # The stamp it answers is the next edit's.
    again = await call(world, "knowledge.get", name="merge-after-green")
    assert out["stamp"] == again["stamp"]
    # The index sees it at once.
    rows = await call(world, "knowledge.list", query="every check")
    assert [r["name"] for r in rows["items"]] == ["merge-after-green"]


async def test_put_refuses_a_stale_stamp(world):
    app = world.app
    await seed(world, mem("deploy-host", "Where the bot runs"))
    got = await call(world, "knowledge.get", name="deploy-host")
    await seed(world, mem("deploy-host", "The bot runs on the small host"))
    now = text_of(world, "deploy-host")
    err = await refused(
        world, "knowledge.put", name="deploy-host", text=got["text"], stamp=got["stamp"]
    )
    assert err.code == "stale" and text_of(world, "deploy-host") == now
    # A hand edit of the file is a change too.
    fresh = await call(world, "knowledge.get", name="deploy-host")
    path = app.knowledge.store.get(app.user, "deploy-host").path
    path.write_text(now + "edited by hand\n")
    err = await refused(
        world,
        "knowledge.put",
        name="deploy-host",
        text=fresh["text"],
        stamp=fresh["stamp"],
    )
    assert err.code == "stale" and path.read_text().endswith("edited by hand\n")


async def test_put_refuses_a_text_that_does_not_parse(world):
    await seed(world, mem("deploy-host"))
    got = await call(world, "knowledge.get", name="deploy-host")
    err = await refused(
        world, "knowledge.put", name="deploy-host", text="no front", stamp=got["stamp"]
    )
    assert err.code == "bad_params" and "frontmatter" in err.message
    assert text_of(world, "deploy-host") == got["text"]


async def test_put_keeps_a_skills_other_files(world):
    app = world.app
    await seed(world, skill("release"), extra={"go.py": b"print(1)"})
    got = await call(world, "knowledge.get", name="release")
    text = got["text"].replace("1. do release", "1. tag\n2. push")
    await call(world, "knowledge.put", name="release", text=text, stamp=got["stamp"])
    assert app.knowledge.store.files(app.user, "release") == ["SKILL.md", "go.py"]
    assert "2. push" in app.knowledge.store.get(app.user, "release").body


async def test_history_lists_each_commit_and_restore_brings_back_an_old_one(world):
    app = world.app
    await seed(world, mem("deploy-host", "first"))
    first = text_of(world, "deploy-host")
    await seed(world, mem("deploy-host", "second"))
    rows = (await call(world, "knowledge.history", name="deploy-host"))["history"]
    assert [r["message"] for r in rows] == [
        "update memory deploy-host",
        "save memory deploy-host",
    ]
    assert rows[0]["kind"] == "memory" and rows[0]["by"] == "test"
    out = await call(world, "knowledge.restore", name="deploy-host", sha=rows[1]["sha"])
    assert text_of(world, "deploy-host") == first
    rows = (await call(world, "knowledge.history", name="deploy-host"))["history"]
    assert rows[0]["sha"] == out["sha"]
    assert rows[0]["message"].startswith("restore memory deploy-host ")
    hits = (await app.knowledge.index(app.user)).rank("first", "memory")
    assert [h.name for h in hits] == ["deploy-host"]


async def test_remove_deletes_and_its_history_restores_it(world):
    app = world.app
    await seed(world, skill("release", "Cut a release"), extra={"go.py": b"print(1)"})
    out = await call(world, "knowledge.remove", name="release")
    assert (
        len(out["sha"]) == 40 and app.knowledge.store.get(app.user, "release") is None
    )
    assert (await call(world, "knowledge.list"))["items"] == []
    rows = (await call(world, "knowledge.history", name="release"))["history"]
    assert [r["message"] for r in rows] == [
        "delete skill release",
        "save skill release",
    ]
    # The deletion's own version holds nothing to bring back.
    err = await refused(world, "knowledge.restore", name="release", sha=rows[0]["sha"])
    assert err.code == "bad_params"
    await call(world, "knowledge.restore", name="release", sha=rows[1]["sha"][:12])
    assert app.knowledge.store.files(app.user, "release") == ["SKILL.md", "go.py"]
    assert [r["name"] for r in (await call(world, "knowledge.list"))["items"]] == [
        "release"
    ]


async def test_restore_refuses_a_sha_of_another_item(world):
    await seed(world, mem("deploy-host"), mem("other-fact"))
    other = (await call(world, "knowledge.history", name="other-fact"))["history"]
    err = await refused(
        world, "knowledge.restore", name="deploy-host", sha=other[0]["sha"]
    )
    assert err.code == "not_found"


async def test_the_view_ops_are_not_agent_tools(world):
    s = await world.spawn()
    agent = Caller("agent", log_id=s.log_id)
    for op in ("knowledge.list", "knowledge.remove"):
        params = {} if op == "knowledge.list" else {"name": "x"}
        with pytest.raises(OpError) as e:
            await world.app.registry.call(op, params, agent)
        assert e.value.code == "not_for_agents"


async def test_an_edit_and_a_remove_work_in_a_store_filled_by_hand(world):
    app = world.app
    d = app.knowledge.store.dir(app.user) / "memories"
    d.mkdir(parents=True)
    (d / "deploy-host.md").write_text(
        "---\nname: deploy-host\ndescription: x\n---\nA\n"
    )
    (d / "other-fact.md").write_text("---\nname: other-fact\ndescription: y\n---\nB\n")
    got = await call(world, "knowledge.get", name="deploy-host")
    text = got["text"].replace("A\n", "B\n")
    await call(
        world, "knowledge.put", name="deploy-host", text=text, stamp=got["stamp"]
    )
    assert text_of(world, "deploy-host").endswith("B\n")
    await call(world, "knowledge.remove", name="other-fact")
    assert app.knowledge.store.get(app.user, "other-fact") is None


async def test_the_list_drops_an_item_removed_after_it_was_listed(world):
    await seed(world, mem("deploy-host"), mem("other-fact"))
    names = lambda got: [r["name"] for r in got["items"]]  # noqa: E731
    assert names(await call(world, "knowledge.list")) == ["deploy-host", "other-fact"]
    await call(world, "knowledge.remove", name="other-fact")
    assert names(await call(world, "knowledge.list")) == ["deploy-host"]
