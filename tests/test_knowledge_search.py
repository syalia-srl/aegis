import time
from dataclasses import replace
from pathlib import Path

import pytest

from aegis.knowledge.search import CatalogIndex, Index

from .knowledge_samples import fixture_hit_at_3, fixture_items, mem, skill


def test_name_description_and_body_weights_rank_a_name_match_first():
    ix = Index(
        [
            mem("deploy-host", "where the bot runs"),
            mem("notes", "deploy mentioned once"),
        ]
    )
    assert ix.rank("deploy", "memory")[0].name == "deploy-host"


def test_accents_and_stems_match_across_spanish_and_english():
    ix = Index([skill("configure-dns", "Configuration of DNS records")])
    assert ix.rank("configuración del dominio", "skill")[0].name == "configure-dns"


def test_different_words_across_languages_do_not_match():
    ix = Index([skill("deploy-bot", "Deploy the bot")])
    assert ix.rank("despliegue", "skill") == []


def test_proposed_and_expired_are_searchable_but_not_hintable():
    ix = Index(
        [
            mem("a", "release freeze", status="proposed"),
            mem("b", "release freeze", expires="2000-01-01"),
        ]
    )
    hits = {h.name: h for h in ix.rank("release", "memory")}
    assert set(hits) == {"a", "b"}
    assert hits["a"].status == "proposed" and hits["b"].flags == ["expired"]
    assert ix.rank("release", "memory", hintable_only=True) == []


def test_paths_double_the_score_in_a_matching_cwd():
    ix = Index(
        [mem("x", "merge rules", paths=["/w/repos/bot"]), mem("y", "merge rules")]
    )
    plain = {h.name: h.score for h in ix.rank("merge", "memory")}
    there = {
        h.name: h.score for h in ix.rank("merge", "memory", cwd="/w/repos/bot/src")
    }
    assert there["x"] == pytest.approx(2 * plain["x"]) and there["y"] == plain["y"]


def test_a_relative_path_glob_matches_under_any_root():
    ix = Index([mem("x", "merge rules", paths=["repos/bot"])])
    plain = ix.rank("merge", "memory")[0].score
    assert ix.rank("merge", "memory", cwd="/home/ana/repos/bot")[
        0
    ].score == pytest.approx(2 * plain)


def test_kinds_rank_apart():
    ix = Index([mem("deploy", "deploy"), skill("deploy-bot", "deploy")])
    assert [h.name for h in ix.rank("deploy", "skill")] == ["deploy-bot"]


def test_a_huge_message_ranks_fast():
    ix = Index(fixture_items())
    t = time.perf_counter()
    ix.rank("log line " * 6000, "skill")
    assert time.perf_counter() - t < 0.05


def test_fixture_bench_hit_at_3():
    hits = fixture_hit_at_3()
    print(f"knowledge hit@3 on the fixture library: {hits:.3f}")
    assert hits >= 0.6, (
        hits
    )  # a floor against regressions; the bench prints the exact value


def test_catalog_ranks_homonyms_by_checkout_and_keeps_expired_entries():
    item = mem("restoring", "Calibrate the ledger", expires="2000-01-01")
    items = [
        replace(
            item,
            source_id=root,
            relative_path="restoring.md",
            path=Path(f"/{root}/restoring.md"),
            scope="workspace",
        )
        for root in ("other", "checkout")
    ]
    index = CatalogIndex(items)
    hits, total = index.rank("calibrate", 1, cwd="/checkout/src", worktree="/checkout")
    assert total == 2
    assert hits[0].entry_id == "checkout:restoring.md" and hits[0].context_match
    assert hits[0].matched_terms == ["calib"]


def test_catalog_weights_title_and_description_above_body():
    items = [
        replace(mem("first", "notes", title="Zirconium"), source_id="first"),
        replace(mem("second", "Zirconium", "notes"), source_id="second"),
        replace(mem("third", "notes", "Zirconium"), source_id="third"),
    ]
    hits, _ = CatalogIndex(items).rank("zirconium", 3, cwd=None, worktree=None)
    assert [hit.entry_id for hit in hits] == ["first:", "second:", "third:"]
