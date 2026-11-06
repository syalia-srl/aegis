"""The canary for sign-in recipes (#946): each harness's real login command,
in a scratch home, read by its recipe until the first step a person answers.

A harness release that redraws its login (new words, a new menu) turns this
red before a person's sign-in card stalls on "unknown screen". Nothing is
signed in: the command is stopped at the first step that needs a person.
"""

from __future__ import annotations

import os
import pty
import select
import signal
import struct
import fcntl
import termios
import time
from pathlib import Path

import pytest

from aegis.signin.recipes import Recipe, load_recipes
from aegis.signin.screen import Screen

from .conftest import bin_of

WAIT_S = 60.0


class Login:
    """The recipe's command on a PTY of the recipe's width, its screen read
    as the card's SignIns reads it."""

    def __init__(self, recipe: Recipe, binary: str, home: Path) -> None:
        self.recipe = recipe
        self.screen = Screen(recipe.cols, 40)
        env = {k: v for k, v in os.environ.items() if k not in recipe.unset}
        env |= {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"),
                "XDG_DATA_HOME": str(home / ".local/share"), "XDG_STATE_HOME": str(home / ".local/state"),
                "XDG_CACHE_HOME": str(home / ".cache"), "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "BROWSER": "true", "TERM": "xterm-256color"}  # fmt: skip
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(home)
            os.execvpe(binary, [binary, *recipe.cmd[1:]], env)
        fcntl.ioctl(
            self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, recipe.cols, 0, 0)
        )

    def until(self, step: str) -> dict:
        """Read until the recipe names ``step``; fail on any other end."""
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            ready, _, _ = select.select([self.fd], [], [], 0.5)
            if ready:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    break
                self.screen.feed(data.decode("utf-8", "replace"))
            found = self.recipe.match(self.screen.text())
            if found is not None:
                rule, captures = found
                if rule.step == step:
                    return captures
                assert rule.step != "failed", f"{rule.name}: {self.tail()}"
        pytest.fail(
            f"no {step} step within {WAIT_S:g}s; the screen was:\n{self.tail()}"
        )

    def type(self, text: str) -> None:
        os.write(self.fd, text.encode())

    def tail(self) -> str:
        return "\n".join(r for r in self.screen.rows() if r.strip())[-2000:]

    def stop(self) -> None:
        try:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        except (ProcessLookupError, ChildProcessError):
            pass
        os.close(self.fd)


@pytest.fixture
def login(tmp_path):
    started: list[Login] = []

    def start(harness: str) -> Login:
        home = tmp_path / "home"
        home.mkdir(exist_ok=True)
        lg = Login(load_recipes()[harness], bin_of(harness), home)
        started.append(lg)
        return lg

    yield start
    for lg in started:
        lg.stop()


def test_sign_in_claude_reaches_the_paste_prompt(login):
    found = login("claude-code").until("paste")
    assert found["url"].startswith("https://claude.com/cai/oauth/authorize?")


def test_sign_in_opencode_reaches_the_go_key_prompt(login):
    lg = login("opencode")
    found = lg.until("choose")
    assert {"OpenCode Go", "OpenCode Console"} <= set(found["options"])
    moves = found["rows"].index("OpenCode Go") - found["current"]
    lg.type(("\x1b[B" if moves > 0 else "\x1b[A") * abs(moves))
    time.sleep(0.2)
    lg.type("\r")
    lg.until("paste")
