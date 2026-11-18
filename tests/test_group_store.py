"""The record store behind named groups (#1039): one small JSON file per
server, holding a key, a name, an owner and when it was made. Names are
generated from the handle word lists, trimmed to one line of at most 60
characters, and suffixed on a clash among one owner's groups."""

import re

import pytest

from aegis.group_store import GroupStore, generated_name
from aegis.ops import OpError


def test_create_mints_a_grp_key_and_a_two_word_name(tmp_path):
    store = GroupStore(tmp_path / "groups.json")
    g = store.create(None)
    assert re.match(r"^grp-[0-9a-f]{8}$", g.key)
    assert re.match(r"^[a-z]+-[a-z]+$", g.name)
    assert store.get(g.key) == g
    assert re.match(r"^[a-z]+-[a-z]+$", generated_name())


def test_a_clashing_name_gets_a_suffix(tmp_path):
    store = GroupStore(tmp_path / "groups.json")
    first = store.create("ana", "bots")
    second = store.create("ana", "bots")
    other = store.create("ben", "bots")
    assert (first.name, second.name, other.name) == ("bots", "bots-2", "bots")


def test_rename_refuses_empty_and_multiline(tmp_path):
    store = GroupStore(tmp_path / "groups.json")
    g = store.create("ana", "bots")
    for bad in ("", "a\nb", "x" * 61):
        with pytest.raises(OpError) as e:
            store.rename(g.key, bad)
        assert e.value.code == "bad_name"
    assert store.get(g.key) == g


def test_rename_to_a_name_in_use_gets_a_suffix(tmp_path):
    store = GroupStore(tmp_path / "groups.json")
    store.create("ana", "bots")
    g = store.create("ana", "other")
    assert store.rename(g.key, "bots").name == "bots-2"
    assert store.by_name("ana", "bots").key != g.key


def test_the_store_survives_a_reload(tmp_path):
    path = tmp_path / "groups.json"
    g = GroupStore(path).create("ana", "bots")
    again = GroupStore(path)
    assert again.get(g.key) == g
    assert again.by_name("ana", "bots") == g


def test_delete_forgets_it(tmp_path):
    store = GroupStore(tmp_path / "groups.json")
    g = store.create(None)
    store.delete(g.key)
    assert store.get(g.key) is None
    assert g.key not in store.keys()
