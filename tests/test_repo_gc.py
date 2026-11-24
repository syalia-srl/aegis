"""Disposable artifacts are proven and rechecked, never inferred from age alone."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis import repo_gc
from aegis.cli import app


def command(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True
    )


def repository(root: Path) -> Path:
    repo = root / "repos" / "old"
    repo.mkdir(parents=True)
    command(repo, "init", "-q")
    command(repo, "config", "user.name", "Test")
    command(repo, "config", "user.email", "test@example.invalid")
    (repo / ".gitignore").write_text(".venv/\ntarget/\nnode_modules/\ndata/\n")
    (repo / "pyproject.toml").write_text('[project]\nname="test"\nversion="0.0.0"\n')
    (repo / "Cargo.toml").write_text('[package]\nname="test"\nversion="0.0.0"\n')
    (repo / "package.json").write_text('{"name":"test"}')
    (repo / "package-lock.json").write_text("{}")
    command(
        repo,
        "add",
        ".gitignore",
        "pyproject.toml",
        "Cargo.toml",
        "package.json",
        "package-lock.json",
    )
    command(repo, "commit", "-qm", "test fixture")
    env = repo / ".venv"
    env.mkdir()
    (env / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (env / "lib").mkdir()
    (env / "lib" / "package.py").write_bytes(b"a" * 8192)
    target = repo / "target"
    target.mkdir()
    (target / ".rustc_info.json").write_text("{}")
    (target / "app").write_bytes(b"a" * 8192)
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "package.js").write_bytes(b"a" * 8192)
    age(repo)
    return repo


def age(repo):
    old = time.time() - 45 * repo_gc.DAY
    for base, dirs, files in os.walk(repo):
        dirs[:] = [x for x in dirs if x != ".git"]
        for name in files + dirs:
            os.utime(Path(base) / name, (old, old), follow_symlinks=False)


def row(inventory, suffix):
    return next(r for r in inventory["rows"] if r["path"].endswith(suffix))


def test_inventory_is_read_only_and_selection_deletes_only_the_chosen_path(tmp_path):
    repo = repository(tmp_path)
    before = command(repo, "status", "--porcelain=v1").stdout
    inventory = repo_gc.scan(tmp_path)
    assert len(inventory["rows"]) == 3
    assert not any(x["reason"] for x in inventory["rows"])
    assert (repo / "target").is_dir()
    result = repo_gc.execute(inventory, [str(repo / "target")])
    assert result["removed"] == [str(repo / "target")]
    assert not result["errors"] and result["tracked_state_preserved"]
    assert not (repo / "target").exists()
    assert (repo / ".venv").is_dir() and (repo / "node_modules").is_dir()
    assert command(repo, "status", "--porcelain=v1").stdout == before


@pytest.mark.parametrize(
    "change,reason",
    [
        ("source", "source"),
        ("artifact", "artifact"),
        ("tracked", "tracked"),
        ("persistent", "unclassified"),
    ],
)
def test_exclusions_are_explained(tmp_path, change, reason):
    repo = repository(tmp_path)
    if change == "source":
        (repo / "pyproject.toml").touch()
    elif change == "artifact":
        (repo / ".venv" / "pyvenv.cfg").touch()
    elif change == "tracked":
        command(repo, "add", "-f", ".venv/pyvenv.cfg")
    else:
        (repo / ".venv" / "personal.sqlite").write_text("keep")
    inventory = repo_gc.scan(tmp_path)
    assert reason in row(inventory, "/.venv")["reason"]


def test_process_and_installed_launcher_references_exclude_artifacts(
    tmp_path, monkeypatch
):
    repo = repository(tmp_path)
    monkeypatch.setattr(repo_gc, "users", lambda: ({repo / ".venv/bin/python"}, []))
    assert "process" in row(repo_gc.scan(tmp_path), "/.venv")["reason"]
    monkeypatch.setattr(
        repo_gc,
        "users",
        lambda: (set(), [f"#!/bin/sh\nexec {repo}/.venv/bin/python".encode()]),
    )
    assert "launcher" in row(repo_gc.scan(tmp_path), "/.venv")["reason"]


def test_active_worktree_is_excluded(tmp_path):
    repo = repository(tmp_path)
    assert (
        "active session"
        in row(repo_gc.scan(tmp_path, active=(repo,)), "/target")["reason"]
    )


@pytest.mark.parametrize("change", ["file", "source", "replacement", "process"])
def test_execution_revalidates_and_skips_newly_active_or_replaced_paths(
    tmp_path, monkeypatch, change
):
    repo = repository(tmp_path)
    inventory = repo_gc.scan(tmp_path)
    target = repo / "target"
    if change == "file":
        (target / "app").write_text("new")
    elif change == "source":
        (repo / "Cargo.toml").touch()
    elif change == "replacement":
        target.rename(repo / "saved")
        target.symlink_to(repo / "saved", target_is_directory=True)
    else:
        monkeypatch.setattr(repo_gc, "users", lambda: ({target / "app"}, []))
    result = repo_gc.execute(inventory, [str(target)])
    assert not result["removed"] and result["skipped"]
    assert target.exists()


def test_arbitrary_ignored_data_and_outside_paths_are_never_selected(tmp_path):
    repo = repository(tmp_path)
    (repo / "data").mkdir()
    (repo / "data" / "original.sqlite").write_text("keep")
    inventory = repo_gc.scan(tmp_path)
    assert not any(x["path"].endswith("/data") for x in inventory["rows"])
    for paths in (
        [],
        [str(repo / "data")],
        [str(tmp_path.parent)],
        [str(repo / "target")] * 2,
    ):
        with pytest.raises(ValueError):
            repo_gc.execute(inventory, paths)
    assert (repo / "data" / "original.sqlite").read_text() == "keep"


def test_shared_hardlinks_reduce_estimated_reclaimed_bytes(tmp_path):
    repo = repository(tmp_path)
    p = repo / ".venv/lib/package.py"
    external = tmp_path / "shared-cache.py"
    os.link(p, external)
    inventory = repo_gc.scan(tmp_path)
    env = row(inventory, "/.venv")
    assert env["bytes"] > env["reclaimable_bytes"]
    result = repo_gc.execute(inventory, [str(repo / ".venv")])
    assert result["removed"] and external.read_bytes() == b"a" * 8192


def test_cli_json_is_read_only_and_apply_cannot_bypass_interactive_approval(tmp_path):
    repo = repository(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("{}")
    runner = CliRunner()
    result = runner.invoke(app, ["gc", "--repos", "--root", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.output)["rows"]) == 3
    result = runner.invoke(
        app, ["gc", "--repos", "--root", str(tmp_path), "--apply"], input="1\ny\n"
    )
    assert result.exit_code != 0 and "interactive" in result.output
    assert (repo / "target").is_dir()


def test_real_process_and_launcher_inspection(tmp_path, monkeypatch):
    repo = repository(tmp_path)
    bins = tmp_path / "bin"
    bins.mkdir()
    (bins / "linked-app").symlink_to(repo / "target/app")
    (bins / "script-app").write_text(f"#!/bin/sh\nexec {repo}/.venv/bin/python\n")
    monkeypatch.setenv("PATH", str(bins))
    process = subprocess.Popen(
        [sys.executable, "-c", "print('ready', flush=True); input()"],
        cwd=repo / "target",
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout.readline().strip() == "ready"
        paths, scripts = repo_gc.users()
        assert repo / "target" in paths
        assert repo / "target/app" in paths
        assert any(str(repo / ".venv/bin/python").encode() in body for body in scripts)
    finally:
        process.communicate("\n", timeout=10)
    assert process.returncode == 0
