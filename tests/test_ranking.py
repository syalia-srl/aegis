import re
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
from pathlib import Path

from aegis.claude.control import Model
from aegis.opencode.config import catalog_from
from aegis.ranking import Ranking, Score, annotate, ranking


def small() -> Ranking:
    return Ranking.from_dict(
        {
            "refreshed": "2026-10-18",
            "models": {
                "a": {"aa": "a", "coding": 41.2, "intelligence": 54.0},
                "b": {"aa": "b", "coding": 38.9, "intelligence": 50.1},
                "c": {"aa": "c", "coding": 45.0, "intelligence": 50.1},
                "d": {"aa": "d", "coding": None, "intelligence": 60.0},
                "e": {"aa": "e", "coding": 70.0, "intelligence": None},
            },
        }
    )


def test_order_puts_higher_intelligence_first_and_unranked_last():
    r = small()
    assert r.order(["x", "e", "b", "y", "a", "d"]) == ["d", "a", "b", "x", "e", "y"]
    assert r.score("a") == Score("a", 41.2, 54.0, None, None)
    assert r.score("x") is None


def test_coding_breaks_an_intelligence_tie():
    assert small().order(["b", "c", "a"]) == ["a", "c", "b"]


def test_rank_counts_only_ranked_models_among_the_given_ones():
    r = small()
    among = ["x", "b", "a", "d", "e"]
    assert r.rank("d", among) == 1  # no Coding Index, still ranked
    assert r.rank("a", among) == 2
    assert r.rank("b", among) == 3
    assert r.rank("x", among) is None
    assert r.rank("e", among) is None  # no Intelligence Index: unranked
    assert r.rank("c", among) is None  # ranked, but not among them


def test_the_packaged_file_loads_and_has_a_date():
    r = ranking()
    assert r is ranking()
    # Empty until the first refresh with an Artificial Analysis key.
    assert r.refreshed == "" or re.fullmatch(r"\d{4}-\d{2}-\d{2}", r.refreshed)
    for model in r.models:
        assert r.score(model) is not None


def refresh_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "refresh-model-ranking"
    loader = SourceFileLoader("refresh_model_ranking", str(path))
    module = module_from_spec(spec_from_loader(loader.name, loader))
    loader.exec_module(module)
    return module


def test_slug_for_maps_catalog_ids(monkeypatch):
    s = refresh_script()
    known = {"kimi-k2-5", "glm-5", "claude-opus-5-5", "deepseek-v3-2-reasoning"}
    assert s.slug_for("opencode/kimi-k2.5-free", "", known) == "kimi-k2-5"
    assert s.slug_for("opencode-go/GLM-5", "", known) == "glm-5"
    # Claude Code names an alias; the resolved id is what normalizes.
    assert s.slug_for("opus", "claude-opus-5-5", known) == "claude-opus-5-5"
    assert s.slug_for("Claude Opus 5.5", "", known) == "claude-opus-5-5"
    assert s.slug_for("opencode/big-pickle", "", known) is None
    monkeypatch.setitem(s.ALIASES, "opencode/deepseek-r", "deepseek-v3-2-reasoning")
    assert s.slug_for("opencode/deepseek-r", "", known) == "deepseek-v3-2-reasoning"


def priced() -> Ranking:
    return Ranking.from_dict(
        {
            "refreshed": "2026-10-18",
            "models": {
                "opus": {"aa": "o", "coding": 72.0, "intelligence": 75.3,
                         "price_in": 15.0, "price_out": 75.0},
                "sonnet": {"aa": "s", "coding": 64.1, "intelligence": 68.0,
                           "price_in": 3.0, "price_out": 15.0},
                "go/free": {"aa": "k", "coding": 41.2, "intelligence": 54.0,
                            "price_in": 0.6, "price_out": 2.5},
            },
        }
    )  # fmt: skip


def bare(value: str, **kw) -> Model:
    return Model(value, value, value, "", (), **kw)


def test_annotate_prefers_the_harness_price():
    opus, mine = annotate(
        [bare("opus"), bare("sonnet", price_in=2.0, price_out=9.0)], priced()
    )
    assert opus.price_source == "Artificial Analysis list price"
    assert mine.price_source == "local harness"
    assert (opus.price_in, opus.price_out) == (15.0, 75.0)  # the file's
    assert (mine.price_in, mine.price_out) == (2.0, 9.0)  # the harness's
    assert (opus.coding, opus.intelligence) == (72.0, 75.3)


def test_annotate_ranks_within_the_harness():
    # The ranks count only the models given, which are one harness's catalog.
    sonnet, new, opus = annotate([bare("sonnet"), bare("new"), bare("opus")], priced())
    assert [m.rank for m in (opus, sonnet, new)] == [1, 2, None]
    assert (new.coding, new.price_in) == (None, None)
    (alone,) = annotate([bare("sonnet")], priced())
    assert alone.rank == 1


def test_a_free_model_wires_free_true_and_zero_price():
    cat = catalog_from([], [], [{
        "id": "free", "providerID": "go", "name": "Free",
        "capabilities": {"tools": True, "output": ["text"]},
        "cost": [{"input": 0, "output": 0}],
    }])  # fmt: skip
    (m,) = annotate(cat.models, priced())
    w = m.wire()
    assert (w["free"], w["price_in"], w["price_out"]) == (True, 0, 0)
    assert (w["rank"], w["coding"], w["intelligence"]) == (1, 41.2, 54.0)


def test_annotate_reads_the_packaged_file_by_default():
    (m,) = annotate([bare("no-such/model")])
    assert m.wire()["rank"] is None


def _file(path: Path, refreshed: str, intelligence: float | None = 50.0) -> Path:
    import json

    path.write_text(
        json.dumps(
            {
                "refreshed": refreshed,
                "models": {"opus": {"aa": "x", "intelligence": intelligence}},
            }
        )
    )
    return path


def test_download_writes_a_valid_newer_file_and_ranking_reads_it(tmp_path):
    from aegis import ranking as rk

    feed = _file(tmp_path / "feed.json", "2999-01-01", 99.0)
    got = tmp_path / "state" / "models.json"
    try:
        rk.use(got)
        assert rk.download(got, feed.as_uri())
        assert rk.ranking().refreshed == "2999-01-01"
        assert rk.ranking().score("opus").intelligence == 99.0
    finally:
        rk.use(None)
    assert rk.ranking().refreshed != "2999-01-01"


def test_download_refuses_a_bad_or_older_file_and_keeps_the_last_good(tmp_path):
    from aegis import ranking as rk

    got = _file(tmp_path / "models.json", "2999-01-02")
    bad = tmp_path / "bad.json"
    bad.write_text("<html>rate limited</html>")
    unscored = _file(tmp_path / "unscored.json", "2999-01-03", None)
    older = _file(tmp_path / "older.json", "2999-01-01")
    for url in (
        bad.as_uri(),
        unscored.as_uri(),
        older.as_uri(),
        "http://127.0.0.1:9/x",
    ):
        assert not rk.download(got, url)
    assert '"2999-01-02"' in got.read_text()
    assert not rk.download(got, "")  # AEGIS_MODELS_URL="" turns it off


def test_a_download_older_than_the_packaged_file_is_ignored(tmp_path):
    from aegis import ranking as rk

    packaged = rk.ranking().refreshed
    try:
        rk.use(_file(tmp_path / "models.json", "2000-01-01"))
        assert rk.ranking().refreshed == packaged
    finally:
        rk.use(None)


def test_model_ranking_config_key():
    from aegis.config import Snapshot

    assert Snapshot.parse(Path("x"), None, {}).model_ranking is True
    off = Snapshot.parse(Path("x"), None, {"model_ranking": False})
    assert off.model_ranking is False


def test_public_offers_keep_unknown_prices_distinct_from_free():
    s = refresh_script()
    offers = s.offers_from(
        {
            "zen": {
                "name": "Zen",
                "models": {
                    "free": {"cost": {"input": 0, "output": 0}, "tool_call": True},
                    "unknown": {},
                    "partial": {"cost": {"input": 0}},
                    "paid": {"cost": {"input": 1, "output": 2}},
                    "old": {"status": "deprecated"},
                },
            }
        }
    )
    assert set(offers) == {"zen/free", "zen/unknown", "zen/partial", "zen/paid"}
    assert offers["zen/free"]["free"] is True
    assert offers["zen/unknown"]["free"] is None
    assert offers["zen/partial"]["free"] is None
    assert offers["zen/paid"]["free"] is False


def test_catalog_only_refresh_preserves_benchmark_date_and_maps_new_providers(
    tmp_path, monkeypatch
):
    import json
    import sys

    s = refresh_script()
    base = tmp_path / "base.json"
    base.write_text(
        json.dumps(
            {
                "refreshed": "2026-01-01",
                "models": {
                    "old/model": {"aa": "model", "intelligence": 50.0},
                },
            }
        )
    )
    out = tmp_path / "out.json"

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"new": {"models": {"model": {"cost": {"input": 0, "output": 0}}}}}

    monkeypatch.setattr(s.httpx, "get", lambda *a, **kw: Response())
    monkeypatch.setattr(
        s,
        "fetch",
        lambda *_: (_ for _ in ()).throw(AssertionError("benchmark API called")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["refresh", "--catalog-only", "--base", str(base), "--out", str(out)],
    )
    assert s.main() == 0
    data = json.loads(out.read_text())
    assert data["refreshed"] == "2026-01-01"
    assert data["catalog_refreshed"].endswith("+00:00")
    assert data["models"]["new/model"]["intelligence"] == 50.0
    assert data["offers"]["new/model"]["free"] is True


def test_empty_provider_refresh_does_not_overwrite_output(tmp_path, monkeypatch):
    import sys

    s = refresh_script()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {}

    out = tmp_path / "out.json"
    out.write_text("last good snapshot")
    monkeypatch.setattr(s.httpx, "get", lambda *a, **kw: Response())
    monkeypatch.setenv("AA_API_KEY", "test")
    monkeypatch.setattr(sys, "argv", ["refresh", "--out", str(out)])
    assert s.main() == 1
    assert out.read_text() == "last good snapshot"


def test_download_rejects_malformed_scores_and_older_offer_timestamp(tmp_path):
    import json
    from aegis import ranking as rk

    got = _file(tmp_path / "models.json", "2999-01-01")
    data = json.loads(got.read_text())
    data["catalog_refreshed"] = "2999-01-01T12:00:00+00:00"
    got.write_text(json.dumps(data))
    feed = tmp_path / "feed.json"
    data["catalog_refreshed"] = "2999-01-01T11:00:00+00:00"
    feed.write_text(json.dumps(data))
    assert not rk.download(got, feed.as_uri())
    data["catalog_refreshed"] = "2999-01-01T13:00:00+00:00"
    data["models"]["bad"] = []
    feed.write_text(json.dumps(data))
    assert not rk.download(got, feed.as_uri())
    del data["models"]["bad"]
    data["models"]["opus"]["intelligence"] = "fast"
    feed.write_text(json.dumps(data))
    assert not rk.download(got, feed.as_uri())
    assert "12:00:00" in got.read_text()


def test_full_refresh_keeps_benchmarks_without_runner_logins(tmp_path, monkeypatch):
    import json
    import sys

    s = refresh_script()

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"provider": {"models": {"fresh-model": {"name": "Fresh Model"}}}}

    async def no_harnesses(*args):
        return []

    monkeypatch.setattr(s.httpx, "get", lambda *a, **kw: Response())
    monkeypatch.setattr(s, "detect", no_harnesses)
    monkeypatch.setattr(
        s,
        "fetch",
        lambda _: {
            "fresh-model": {
                "slug": "fresh-model",
                "evaluations": {"artificial_analysis_intelligence_index": 77},
            }
        },
    )
    monkeypatch.setenv("AA_API_KEY", "test")
    out = tmp_path / "out.json"
    monkeypatch.setattr(sys, "argv", ["refresh", "--out", str(out)])
    assert s.main() == 0
    data = json.loads(out.read_text())
    assert data["models"]["fresh-model"]["intelligence"] == 77
    assert data["models"]["provider/fresh-model"]["intelligence"] == 77
    assert data["offers"]["provider/fresh-model"]["free"] is None
