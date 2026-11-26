"""Source snapshots are incremental and publish in generation order."""

import asyncio
from threading import Event

from aegis.knowledge import sources


async def test_replaced_configuration_discards_an_older_inflight_scan(
    tmp_path, monkeypatch
):
    path = tmp_path / "guide.md"
    path.write_text("old contents")
    source = sources.Source(tmp_path, "markdown")
    registry = sources.Sources()
    registry.configure([source])
    original_scan = sources.scan
    scanned, release = Event(), Event()

    def hold_first(*args):
        result = original_scan(*args)
        if not scanned.is_set():
            scanned.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(sources, "scan", hold_first)
    old = asyncio.create_task(registry.refresh(source, 1, 5))
    try:
        assert await asyncio.to_thread(scanned.wait, 5)
        registry.configure([])
        registry.configure([source])
        path.write_text("new contents")
        fresh = await registry.refresh(source, 2, 5)
    finally:
        release.set()
    assert (await old).entries["guide.md"].text == "old contents"
    assert fresh.entries["guide.md"].text == "new contents"
    assert await registry.refresh(source, 3, 5) is fresh


async def test_refresh_reuses_unchanged_files_and_publishes_deletions(tmp_path):
    keep = tmp_path / "keep.md"
    keep.write_text("keep this")
    remove = tmp_path / "remove.md"
    remove.write_text("remove this")
    source = sources.Source(tmp_path, "markdown")
    registry = sources.Sources()
    original = await registry.refresh(source, 1, 5)
    remove.unlink()
    first, second = await asyncio.gather(
        registry.refresh(source, 7, 5), registry.refresh(source, 7, 5)
    )
    assert first is second
    assert list(first.entries) == ["keep.md"]
    assert first.entries["keep.md"] is original.entries["keep.md"]
