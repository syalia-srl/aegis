"""A real terminal must select paths and confirm before repository gc deletes."""

import os
import pty
import select
import subprocess
import sys
import time

import pytest

from .test_repo_gc import repository

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("answer", ["n", "y"])
def test_terminal_selection_and_confirmation(tmp_path, answer):
    repo = repository(tmp_path)
    (tmp_path / ".aegis.yaml").write_text("{}")
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "aegis",
            "gc",
            "--root",
            str(tmp_path),
            "--repos",
            "--apply",
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    output = b""

    def expect(needle):
        nonlocal output
        deadline = time.monotonic() + 20
        while needle not in output:
            assert time.monotonic() < deadline, output.decode(errors="replace")
            if select.select([master], [], [], 0.1)[0]:
                output += os.read(master, 65536)

    try:
        expect(b"Select candidate numbers")
        assert (repo / ".venv").is_dir()
        os.write(master, b"1\n")
        expect(b"Delete exactly these rebuildable directories?")
        assert (repo / ".venv").is_dir(), "selection alone must not delete"
        os.write(master, (answer + "\n").encode())
        assert process.wait(timeout=20) == 0
        assert (repo / ".venv").exists() is (answer == "n")
        assert (repo / "target").is_dir() and (repo / "node_modules").is_dir()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        os.close(master)
