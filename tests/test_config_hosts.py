"""``hosts:`` in ``.aegis.yaml``: what differs per server, in the shared file."""

from __future__ import annotations

import socket
from pathlib import Path

from ruamel.yaml import YAML

from aegis.config import (
    Config,
    ConfigDoc,
    overlay,
    stamp_of,
    write,
    write_host,
)
from aegis.doctor import _hosts

SHARED = {
    "default_agent": "opus",
    "agents": {
        "opus": {
            "harness": "claude-code",
            "model": "opus",
            "effort": "high",
            "permission": "full",
        },
        "fable": {
            "harness": "claude-code",
            "model": "fable",
            "effort": "high",
            "permission": "full",
        },
    },
    "max_workers": 4,
}


def test_a_block_merges_mappings_replaces_values_and_removes_nulls():
    data = {
        **SHARED,
        "hosts": {
            "laptop": {
                "exit_via": "always-on",
                "agents": {"opus": {"effort": "medium"}, "fable": None},
                "max_workers": 2,
            }
        },
    }
    got = overlay(data, "laptop")
    assert got["exit_via"] == "always-on"
    assert set(got["agents"]) == {"opus"}
    assert got["agents"]["opus"]["effort"] == "medium"
    assert got["agents"]["opus"]["model"] == "opus"
    assert got["max_workers"] == 2
    # The shared mapping is left as it was.
    assert data["agents"]["opus"]["effort"] == "high"


def test_another_servers_block_and_no_name_change_nothing():
    data = {**SHARED, "hosts": {"always-on": {"max_workers": 8}}}
    assert overlay(data, "laptop") == data
    assert overlay(data, None) == data


def test_a_list_in_a_block_replaces_the_whole_list():
    data = {
        "agents": {"cheap": {"harness": "opencode", "models": ["a", "b"]}},
        "hosts": {"laptop": {"agents": {"cheap": {"models": ["c"]}}}},
    }
    assert overlay(data, "laptop")["agents"]["cheap"]["models"] == ["c"]


def test_a_bad_shape_or_a_boot_key_applies_nothing_and_says_why():
    bad = {**SHARED, "hosts": ["laptop"]}
    assert overlay(bad, "laptop") == bad
    boot = {**SHARED, "hosts": {"laptop": {"multi_user": {}, "max_workers": 1}}}
    assert overlay(boot, "laptop") == boot
    host = socket.gethostname()
    [f] = _hosts({**SHARED, "hosts": {host: {"multi_user": {}}}}, host)
    assert f.level == "error" and "multi_user" in f.message


def test_the_doctor_names_the_block_it_applies():
    host = socket.gethostname()
    [f] = _hosts({**SHARED, "hosts": {host: {"max_workers": 2}}}, host)
    assert f.level == "ok" and f"hosts.{host}: max_workers" in f.message
    [g] = _hosts({**SHARED, "hosts": {"elsewhere": {}}}, host)
    assert "no block for" in g.message and "elsewhere" in g.message
    assert _hosts(SHARED, host) == []


def _file(tmp_path, data) -> Path:
    buf = tmp_path / ".aegis.yaml"
    with buf.open("w") as f:
        YAML().dump(data, f)
    return buf


def test_config_reads_its_own_block_and_settings_sees_the_shared_layer(tmp_path):
    _file(tmp_path, {**SHARED, "hosts": {"laptop": {"exit_via": "always-on"}}})
    laptop = Config(tmp_path, host="laptop").current()
    assert laptop.exit_via == "always-on"
    assert laptop.doc.exit_via is None
    assert Config(tmp_path, host="always-on").current().exit_via is None


def test_a_settings_save_keeps_the_hosts_block(tmp_path):
    path = _file(tmp_path, {**SHARED, "hosts": {"laptop": {"max_workers": 2}}})
    snap = Config(tmp_path, host="laptop").current()
    assert snap.max_workers == 2
    doc: ConfigDoc = snap.doc.model_copy(update={"default_agent": "fable"})
    assert write(path, doc, snap.stamp) == []
    data = YAML(typ="safe").load(path.read_text())
    assert data["default_agent"] == "fable"
    assert data["hosts"] == {"laptop": {"max_workers": 2}}


def test_write_host_sets_and_clears_one_servers_key(tmp_path):
    path = _file(tmp_path, dict(SHARED))
    write_host(path, "lap.top", "exit_via", "always-on", stamp_of(path))
    data = YAML(typ="safe").load(path.read_text())
    assert data["hosts"] == {"lap.top": {"exit_via": "always-on"}}
    write_host(path, "lap.top", "exit_via", None, stamp_of(path))
    assert "hosts" not in YAML(typ="safe").load(path.read_text())


def test_clearing_a_shared_key_on_one_server_writes_a_null(tmp_path):
    path = _file(tmp_path, {**SHARED, "exit_via": "always-on"})
    write_host(path, "laptop", "exit_via", None, stamp_of(path))
    data = YAML(typ="safe").load(path.read_text())
    assert data["exit_via"] == "always-on"
    assert data["hosts"] == {"laptop": {"exit_via": None}}
    assert Config(tmp_path, host="laptop").current().exit_via is None
    assert Config(tmp_path, host="other").current().exit_via == "always-on"
