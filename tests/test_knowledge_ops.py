# ruff: noqa: F811  (fixtures imported from test_member_view and test_commands)
"""The memory tools and /remember, through the fake claude's real MCP calls."""

import asyncio
import json
import subprocess
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from aegis.knowledge import history, sources
from aegis.ops import OpError
from aegis.web import PROTO

from .conftest import until
from .knowledge_samples import mem
from .test_agents import mcp, turn
from .test_agents import world as agents_world  # noqa: F401
from .test_commands import send


def ok(said: str):
    assert said.startswith("mcp ok: "), said
    return json.loads(said.removeprefix("mcp ok: "))


async def test_an_agent_saves_searches_reads_and_deletes_a_memory(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    saved = ok(
        await turn(
            s,
            mcp(
                "memory_save",
                name="quill-staging",
                description="The staging host for project quill",
                body="It is ben-staging-2.",
                type="project",
                asked=True,
            ),
            by_person=True,
        )
    )
    assert saved.pop("card")
    assert saved == {"saved": "quill-staging", "status": "approved"}
    hits = ok(await turn(s, mcp("memory_search", query="where is quill staged")))
    assert hits["hits"][0]["name"] == "quill-staging"
    got = ok(await turn(s, mcp("memory_read", names=["quill-staging"])))
    text = got["memories"][0]["text"]
    assert "ben-staging-2" in text and "type: project" in text
    assert f"source: session {s.handle}" in text
    root = app.roots.state_root / "knowledge" / app.user
    assert history.log(root, "memories/quill-staging.md")[0]["message"] == (
        "save memory quill-staging"
    )
    gone = ok(await turn(s, mcp("memory_delete", name="quill-staging")))
    assert gone["deleted"] == "quill-staging" and len(gone["sha"]) == 40
    said = await turn(s, mcp("memory_read", names=["quill-staging"]))
    assert said.startswith("mcp error: not_found"), said


async def test_a_temporary_memory_needs_an_expiry(agents_world):
    s = await agents_world.spawn()
    said = await turn(
        s,
        mcp(
            "memory_save",
            name="freeze",
            description="d",
            body="b",
            lifetime="temporary",
        ),
    )
    assert said.startswith("mcp error: bad_params"), said


async def test_a_read_after_a_hint_records_its_rank(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(
        app.user, mem("bot-host", "the bot runs on host b"), "t"
    )
    s = await agents_world.spawn()
    await turn(s, "where does the bot run")  # the send carries the hint
    ok(await turn(s, mcp("memory_read", names=["bot-host"])))
    rows = app.knowledge.usage.rows(app.user, "bot-host")
    assert [(r["event"], r["detail"]) for r in rows] == [
        ("hinted", "rank=1"),
        ("read", "rank=1"),
    ]


async def test_remember_delivers_one_turn_naming_memory_save(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    lid = s.log_id
    assert await send(app, lid, "/remember the demo is on friday") == {
        "status": "delivered"
    }

    def inbox():
        return [
            e["md"]
            for e in s.entries()
            if e["kind"] == "inbox" and "> from remember" in (e.get("md") or "")
        ]

    await until(inbox, timeout=5)
    (md,) = inbox()
    assert "the demo is on friday" in md
    assert "memory_save" in md and "asked: true" in md


async def test_read_permission_sessions_may_search_but_not_save(agents_world):
    r = await agents_world.app.registry.call("session.spawn", {"agent": "reviewer"})
    s = agents_world.session(r["log_id"])
    ok(await turn(s, mcp("memory_search", query="anything")))
    ok(await turn(s, mcp("knowledge_stats")))
    ok(await turn(s, mcp("knowledge_catalog")))
    said = await turn(s, mcp("memory_save", name="x", description="d", body="b"))
    assert said.startswith("mcp error: read_only"), said


async def test_stats_join_usage_with_items(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(app.user, mem("used"), "t")
    await app.knowledge.store.save(app.user, mem("idle"), "t")
    app.knowledge.usage.add(app.user, "s", "memory", "used", "read")
    s = await agents_world.spawn()
    got = ok(await turn(s, mcp("knowledge_stats", unused_days=30)))
    assert [i["name"] for i in got["items"]] == ["idle"]
    got = ok(await turn(s, mcp("knowledge_stats")))
    assert {i["name"]: i["read"] for i in got["items"]} == {"idle": 0, "used": 1}


async def test_remember_refuses_empty_text_and_read_permission(agents_world):
    app = agents_world.app
    s = await agents_world.spawn()
    for text in ("/remember", "/remember   "):
        with pytest.raises(OpError) as e:
            await send(app, s.log_id, text)
        assert e.value.code == "missing_argument"
    r = await app.registry.call("session.spawn", {"agent": "reviewer"})
    with pytest.raises(OpError) as e:
        await send(app, r["log_id"], "/remember the demo is on friday")
    assert e.value.code == "read_only" and "read permission" in e.value.message


async def test_catalog_search_read_and_revocation_over_mcp_and_browser_rpc(
    agents_world, monkeypatch
):
    root = agents_world.root
    history.ensure_repo(root)
    how = root / "know-how"
    how.mkdir()
    late = how / "restoring.md"
    late.write_text(
        "Routine preparation. " * 40 + "\nZirconium ledger. [[Checklist]]\n"
    )
    (how / "checklist.md").write_text("# Checklist\n[[Restoring]]\n")
    s = await agents_world.spawn()
    query = {"query": "zirconium ledger"}
    search = ok(await turn(s, mcp("knowledge_catalog", **query)))
    assert search["total"] == 1
    (hit,) = search["items"]
    assert hit["relative_path"] == "know-how/restoring.md"
    ref = {"entry_id": hit["id"]}
    read = ok(await turn(s, mcp("knowledge_catalog", **ref)))
    assert read["body"] == late.read_text()
    assert read["revision"] == hit["revision"]
    (link,) = read["links"]
    assert link["status"] == "resolved"
    assert read["backlinks"][0]["id"] == link["target_id"]
    assert read["neighbor_count"] == 1
    target = {"entry_id": link["target_id"]}
    neighbor = ok(await turn(s, mcp("knowledge_catalog", **target)))
    assert neighbor["links"][0]["target_id"] == hit["id"]

    base = agents_world.base
    async with connect(base.replace("http:", "ws:") + "/ws", origin=base) as ws:
        await ws.send(json.dumps({"t": "hello", "token": "tok", "proto": PROTO}))
        assert json.loads(await ws.recv())["t"] == "welcome"

        async def catalog(id, params):
            await ws.send(
                json.dumps(
                    {
                        "t": "call",
                        "id": id,
                        "op": "knowledge.catalog",
                        "params": {"log_id": s.log_id, **params},
                    }
                )
            )
            response = json.loads(await ws.recv())
            assert response["t"] == "reply" and response["id"] == id
            return response

        ws_search = (await catalog(1, query))["result"]
        assert ws_search["total"] == 1
        (ws_hit,) = ws_search["items"]
        assert ws_hit["id"] == hit["id"] and ws_hit["revision"] == hit["revision"]
        assert ws_hit["reads"] == hit["reads"] + 1 and ws_hit["last_used"] is not None
        for request, before in ((ref, read), (target, neighbor)):
            after = (await catalog(2, request))["result"]
            assert after.pop("reads") == before.pop("reads") + 1
            before.pop("last_used")
            assert after.pop("last_used") is not None
            assert after == before

        monkeypatch.setattr(
            agents_world.app.policy,
            "knowledge_source",
            lambda who, session, root: root != how,
        )
        denied = (await catalog(3, query))["result"]
        assert denied["total"] == 0 and denied["items"] == []
        assert (await catalog(4, ref))["error"]["code"] == "not_found"

    denied = ok(await turn(s, mcp("knowledge_catalog", **query)))
    assert denied["total"] == 0 and denied["items"] == []
    assert (await turn(s, mcp("knowledge_catalog", **ref))).startswith(
        "mcp error: not_found"
    )


async def test_agent_reads_personal_catalog_hit_by_id(agents_world):
    app = agents_world.app
    await app.knowledge.store.save(
        app.user,
        mem(
            "calibration-log",
            "Routine preparation",
            "Maintenance notes. " * 40 + "\nZirconium ledger.",
            expires="2000-01-01",
        ),
        "test",
    )
    session = await agents_world.spawn()
    result = ok(await turn(session, mcp("knowledge_catalog", query="zirconium ledger")))
    assert result["total"] == 1
    (hit,) = result["items"]
    assert hit["scope"] == "personal" and hit["expired"]
    assert hit["relative_path"] == "memories/calibration-log.md"
    read = ok(await turn(session, mcp("knowledge_catalog", entry_id=hit["id"])))
    assert read["id"] == hit["id"] and read["revision"] == hit["revision"]
    assert read["body"].strip().endswith("Zirconium ledger.")


async def test_agents_write_selected_pages_and_personal_reminder(
    agents_world, monkeypatch
):
    w = agents_world
    root = w.root
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    how = root / "know-how"
    how.mkdir()
    procedure = how / "release.md"
    procedure.write_text("---\nwhen: Releasing\n---\nCheck the backup.\n")
    skill = root / ".codex" / "skills" / "release"
    skill.mkdir(parents=True)
    skill_page = skill / "SKILL.md"
    skill_page.write_text(
        "---\nname: release\ndescription: Release steps\n---\nFollow the procedure.\n"
    )
    attachment = skill / "notes.txt"
    attachment.write_bytes(b"original notes")
    with (root / ".aegis.yaml").open("a") as config:
        config.write(
            "knowledge:\n  sources:\n    - root: .codex/skills\n      format: skill\n      include: [release/SKILL.md]\n"
        )
    first = await w.spawn()
    second = await w.spawn()

    async def call(session, tool, **params):
        return await turn(session, mcp(tool, **params), by_person=True)

    pages = ok(await call(first, "knowledge_catalog"))["items"]
    by_path = {page["relative_path"]: page for page in pages}
    doc = by_path["know-how/release.md"]
    bundle = by_path["release/SKILL.md"]
    assert doc["writable"] and not bundle["writable"]
    assert doc["id"] != bundle["id"] and bundle["files"] == ["notes.txt"]

    def edit(page, text):
        return dict(entry_id=page["id"], expected_revision=page["revision"], text=text)

    skill_text = (
        "---\nname: release\ndescription: Release steps\n---\nConfirm recovery.\n"
    )
    refused = await call(first, "knowledge_write", **edit(bundle, skill_text))
    assert refused.startswith("mcp error: forbidden"), refused
    assert "Follow the procedure." in skill_page.read_text()
    with (root / ".aegis.yaml").open("a") as config:
        config.write("      writable: true\n")
    with monkeypatch.context() as patch:
        patch.setattr(w.app.policy, "knowledge_source_write", lambda *_: False)
        refused = await call(first, "knowledge_write", **edit(bundle, skill_text))
        assert refused.startswith("mcp error: forbidden"), refused
        assert "Follow the procedure." in skill_page.read_text()
    saved = ok(await call(first, "knowledge_write", **edit(bundle, skill_text)))
    assert saved["path"] == str(skill_page)
    assert (
        skill_page.read_text() == skill_text
        and attachment.read_bytes() == b"original notes"
    )
    refused = await call(
        first,
        "knowledge_write",
        **edit(
            {**bundle, "id": bundle["id"].replace("SKILL.md", "notes.txt")}, "new notes"
        ),
    )
    assert refused.startswith("mcp error: not_found"), refused
    assert attachment.read_bytes() == b"original notes"

    variants = [
        "---\nwhen: Releasing\n---\nValidate the zirconium restore.\n",
        "---\nwhen: Releasing\n---\nValidate the zirconium recovery.\n",
    ]
    replies = await asyncio.gather(
        *(
            call(session, "knowledge_write", **edit(doc, text))
            for session, text in zip((first, second), variants, strict=True)
        )
    )
    (written,) = [ok(reply) for reply in replies if reply.startswith("mcp ok: ")]
    (conflict,) = [reply for reply in replies if reply.startswith("mcp error: stale")]
    assert written["revision"] in conflict and written["path"] == str(procedure)
    winner = next(i for i, reply in enumerate(replies) if reply.startswith("mcp ok: "))
    assert procedure.read_text() == variants[winner]
    found = ok(await call(second, "knowledge_catalog", query="zirconium"))["items"]
    assert [page["id"] for page in found] == [doc["id"]]
    assert found[0]["revision"] == written["revision"]
    ok(await call(second, "knowledge_catalog", entry_id=doc["id"]))
    used = await asyncio.to_thread(w.app.knowledge.usage.summary, w.app.user)
    assert used[doc["id"]]["read"] == 1 and used[bundle["id"]]["read"] == 0

    # A separate editor changes the page after Aegis read its expected
    # revision, immediately before publication.
    external = "---\nwhen: Releasing\n---\nExternal editor's version.\n"
    during_restore = "# A second edit while restoring\n"
    exchange = sources._exchange

    def race(fd, first, second):
        if first.startswith(sources.WRITE_TEMP) and second == procedure.name:
            procedure.write_text(
                external
                if procedure.read_text() != "# Proposed edit\n"
                else during_restore
            )
        return exchange(fd, first, second)

    with monkeypatch.context() as patch:
        patch.setattr(sources, "_exchange", race)
        refused = await call(
            first, "knowledge_write", **edit(written, "# Proposed edit\n")
        )
    revision = sha256(external.encode()).hexdigest()
    assert refused.startswith("mcp error: stale") and revision in refused
    assert procedure.read_text() == external
    (recovery,) = how.glob(f"{sources.WRITE_TEMP}*")
    assert str(recovery) in refused and recovery.read_text() == during_restore

    # An in-place editor already holding the original inode stays attached
    # to the real path when Aegis refuses to retire that inode.
    with procedure.open("r+") as editor:
        refused = await call(
            first,
            "knowledge_write",
            **edit({**written, "revision": revision}, variants[1]),
        )
        assert refused.startswith("mcp error: write_conflict"), refused
        editor.write("# Continued edit\n")
    assert procedure.read_text().startswith("# Continued edit")

    params = dict(
        name="restore-this-week",
        description="Personal restore assignment",
        body=f"I will run the restore this week. See [[{doc['id']}]].",
        lifetime="temporary",
        expires=(date.today() + timedelta(days=5)).isoformat(),
        paths=[str(root)],
        asked=True,
        expected_revision=None,
    )
    saved = ok(await call(first, "memory_save", **params))
    assert saved["status"] == "approved" and saved["card"]
    memory = Path(saved["path"])
    assert memory.is_relative_to(w.app.roots.state_root)
    assert params["expires"] in memory.read_text()
    assert doc["id"] in memory.read_text()
    refused = await call(second, "memory_save", **params)
    assert refused.startswith("mcp error: stale") and saved["revision"] in refused
    assert history.log(memory.parent.parent, "memories/restore-this-week.md")[0][
        "message"
    ] == ("save memory restore-this-week")


@pytest.mark.parametrize("mode", ["ok", "no-match", "fail", "slow"])
async def test_task_synthesis_from_the_agent_inbox(
    tmp_path, fake_claude, monkeypatch, mode
):
    from .test_agents import CONFIG, World

    history.ensure_repo(tmp_path)
    config = CONFIG.replace(
        "queues:\n", "queues:\n  knowledge-synthesis: {agent: opus}\n"
    )
    (tmp_path / ".aegis.yaml").write_text(
        config + "knowledge: {sources: [{root: guides}]}\n"
    )
    how = tmp_path / "know-how"
    guides = tmp_path / "guides"
    how.mkdir()
    guides.mkdir()
    first = how / "recovery.md"
    second = guides / "retention.md"
    first.write_text(
        "# Recovery\nKeep the zirconium ledger for seven days. [[Retention]]\n"
    )
    second.write_text("# Retention\nKeep the ledger for thirty days. [[Recovery]]\n")
    monkeypatch.setenv("FAKE_CLAUDE_ONESHOT", mode)
    world = await World(tmp_path, fake_claude).start()
    try:
        s = await world.spawn()
        app = world.app
        reply = ok(
            await turn(
                s,
                mcp(
                    "knowledge_synthesize",
                    task="nonexistentfruit" if mode == "no-match" else "zirconium",
                ),
            )
        )
        task = app.queues.tasks[reply["task_id"]]
        if mode == "slow":
            await until(lambda: task.status == "running")
            await app.queues.cancel(task)
            assert task.status == "cancelled"
        else:
            await until(lambda: task.status in ("completed", "failed"))

        def delivered():
            return [
                e
                for e in s.entries()
                if e["kind"] == "inbox" and f"task#{task.id}" in (e.get("md") or "")
            ]

        await until(lambda: delivered() and not s.busy)
        await app.queues._callback(task, "ok", "duplicate result")
        assert len(delivered()) == 1
        assert "duplicate result" not in str(s.entries()) + str(s.held)
        if mode in ("ok", "no-match"):
            guide = json.loads(task.result)
            if mode == "ok":
                assert [c["relative_path"] for c in guide["citations"]] == [
                    "know-how/recovery.md",
                    "retention.md",
                ]
                assert guide["contradictions"][0]["sources"] == [1, 2]
                for citation in guide["citations"]:
                    params = {
                        "entry_id": citation["id"],
                        "expected_revision": citation["revision"],
                    }
                    page = ok(await turn(s, mcp("knowledge_catalog", **params)))
                    assert page["revision"] == citation["revision"]
                first.write_text(first.read_text() + "Changed.\n")
                answer = await turn(
                    s,
                    mcp(
                        "knowledge_catalog",
                        entry_id=guide["citations"][0]["id"],
                        expected_revision=guide["citations"][0]["revision"],
                    ),
                )
                assert answer.startswith("mcp error: revision_changed")
            else:
                assert guide["citations"] == []
                assert guide["gaps"] == ["No matching visible Knowledge pages."]
        if mode == "fail":
            assert task.status == "failed"
        assert set(how.iterdir()) == {first}
        assert set(guides.iterdir()) == {second}
        assert not await app.knowledge.items(app.user)
    finally:
        await world.stop()
