"""A group's generated name and title (#1047): the generator agent names a
group again each time its members change, never a pinned name, once per
membership, and not at all without ``group_names:``."""

import pytest

from aegis import group_names

from .conftest import until
from .test_agents import CONFIG, World


@pytest.fixture
async def world(tmp_path, fake_claude, monkeypatch):
    monkeypatch.setattr(group_names, "DEBOUNCE_S", 0.05)
    (tmp_path / ".aegis.yaml").write_text(CONFIG + "group_names: {agent: opus}\n")
    w = await World(tmp_path, fake_claude).start()
    yield w
    await w.stop()


def calls(tmp_path) -> int:
    log = tmp_path / "fake-home" / "oneshot.log"
    return len(log.read_text().splitlines()) if log.exists() else 0


async def test_joining_names_the_group_and_titles_it_on_every_card(world):
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id], "new", world.app.user)
    await until(lambda: reg.groups.get(key).name == "crew-of-2")
    g = reg.groups.get(key)
    assert g.title == "group of " + ", ".join(sorted([a.handle, b.handle]))
    assert not g.pinned
    assert a.wire()["group_title"] == b.wire()["group_title"] == g.title
    c = await world.spawn()
    reg.set_group([c.log_id], key, world.app.user)
    await until(lambda: reg.groups.get(key).name == "crew-of-3")


async def test_a_leaving_member_renames_it_again(world):
    reg = world.app.sessions
    a, b, c = await world.spawn(), await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id, c.log_id], "new", world.app.user)
    await until(lambda: reg.groups.get(key).name == "crew-of-3")
    await reg.close(c.log_id)
    await until(lambda: reg.groups.get(key).name == "crew-of-2")


async def test_a_pinned_name_keeps_its_name_and_gets_a_title(world):
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    key = reg.set_group([a.log_id, b.log_id], "new", world.app.user)
    reg.rename_group(key, "my own name", world.app.user)
    c = await world.spawn()
    reg.set_group([c.log_id], key, world.app.user)
    await until(lambda: reg.groups.get(key).title.count(",") == 2)
    assert reg.groups.get(key).name == "my own name"


async def test_a_burst_of_joins_makes_one_call(world, tmp_path, monkeypatch):
    monkeypatch.setattr(group_names, "DEBOUNCE_S", 0.5)
    reg = world.app.sessions
    a, b = await world.spawn(), await world.spawn()
    before = calls(tmp_path)
    key = reg.set_group([a.log_id], "new", world.app.user)
    reg.set_group([b.log_id], key, world.app.user)
    await until(lambda: reg.groups.get(key).name == "crew-of-2")
    assert calls(tmp_path) == before + 1


async def test_without_the_key_nothing_is_named(tmp_path, fake_claude):
    (tmp_path / ".aegis.yaml").write_text(CONFIG)
    w = await World(tmp_path, fake_claude).start()
    try:
        reg = w.app.sessions
        a, b = await w.spawn(), await w.spawn()
        key = reg.set_group([a.log_id, b.log_id], "new", w.app.user)
        assert w.app.group_names._tasks == {}
        assert not reg.groups.get(key).title
    finally:
        await w.stop()


def test_clean_keeps_a_valid_name_and_one_line_title():
    out = group_names.GroupNameOut(name="Docs-Sweep", title="Sweep\nthe   docs.")
    assert group_names.clean(out) == ("docs-sweep", "Sweep the docs")
    bad = group_names.GroupNameOut(name="one", title="x" * 200)
    name, title = group_names.clean(bad)
    assert name is None and len(title) == group_names.TITLE_MAX
