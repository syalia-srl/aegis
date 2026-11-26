import asyncio
import json
import resource
import sys
import tempfile
import time
import tracemalloc
from dataclasses import replace
from pathlib import Path

from aegis.app import App
from aegis.knowledge.history import ensure_repo
from aegis.ops import USER
from tests.knowledge_samples import LIBRARY, fixture_hit_at_3, fixture_items
from aegis.roots import make_roots

async def run(mode):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ensure_repo(root)
        how = root / "know-how"
        how.mkdir()
        for i in range(400):
            body = f"# Procedure {i}\n" + "Routine preparation and recovery checks. " * 100
            (how / f"procedure-{i:03}.md").write_text(body + f"\nCalibration token{i:03d}.\n")
        app = App(make_roots(root, None))
        log_id = "20261126-000000-aaaaaa"
        app.sessions.archived[log_id] = {"cwd": str(root), "archived": True}
        cases = [
            ("cold scan", lambda: app._refresh_knowledge_sources()),
            ("first catalog", lambda: app.registry.call(
                "knowledge.catalog", {"log_id": log_id, **({"query": "calibration"} if mode == "head" else {})}, USER)),
            ("warm catalog", lambda: app.registry.call(
                "knowledge.catalog", {"log_id": log_id, **({"query": "calibration"} if mode == "head" else {})}, USER)),
        ]
        tracemalloc.start()
        for label, action in cases:
            before = tracemalloc.get_traced_memory()[0]
            tracemalloc.reset_peak()
            start = time.perf_counter()
            result = await action()
            elapsed = (time.perf_counter() - start) * 1000
            retained, peak = tracemalloc.get_traced_memory()
            count = result.get("total", len(result["items"])) if isinstance(result, dict) else result
            print(f"{mode:4} {label:14} {elapsed:8.1f} ms {peak / 1024:8.1f} KiB peak {max(0, peak-before)/1024:8.1f} KiB incremental {retained/1024:8.1f} KiB retained {count:3} results/roots")
        tracemalloc.stop()
        print(f"{mode:4} peak RSS {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss} KiB")
        print(f"{mode:4} legacy hit@3 {fixture_hit_at_3():.3f}")
        if mode == "head":
            from aegis.knowledge.search import CatalogIndex
            index = CatalogIndex([replace(it, source_id=it.kind, relative_path=it.name) for it in fixture_items()])
            queries = json.loads((LIBRARY / "queries.json").read_text())
            hits = 0
            for query in queries:
                ranked, _ = index.rank(query["query"], 3, cwd=None, worktree=None)
                relevant = {f"{query['kind']}:{name}" for name in query["relevant"]}
                hits += bool(relevant.intersection(hit.entry_id for hit in ranked))
            print(f"{mode:4} common hit@3 {hits / len(queries):.3f} across {len(queries)} queries")

asyncio.run(run(sys.argv[1]))
