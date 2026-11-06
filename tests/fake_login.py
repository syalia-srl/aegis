"""A stand-in for `claude setup-token` (#946): replays the recorded screens.

Prints the recording up to the paste prompt, reads a line; FAKECODE replays the
rest (ending in the FAKE token) and exits 0, anything else replays the
bad-code recording's error and exits 1. FAKE_LOGIN_SILENT=1 prints a screen no
recipe knows and waits.
"""

import json
import os
import sys
import termios
import time
from pathlib import Path

DATA = Path(__file__).parent / "data" / "sign-in" / "claude"


def chunks(name: str) -> list[str]:
    return [json.loads(line)["data"] for line in (DATA / name).read_text().splitlines()]


def play(parts: list[str]) -> None:
    for part in parts:
        sys.stdout.write(part)
        sys.stdout.flush()


if os.environ.get("FAKE_LOGIN_SILENT"):
    play(["Something new happened.\r\n"])
    time.sleep(300)
    sys.exit(0)

if sys.stdin.isatty():
    attrs = termios.tcgetattr(0)
    attrs[3] &= ~termios.ECHO
    termios.tcsetattr(0, termios.TCSANOW, attrs)

if sys.argv[1:2] == ["opencode"]:
    # opencode auth login, OpenCode Go: the menu, a choice, the key prompt, a key.
    go = [
        json.loads(line)["data"]
        for line in (DATA.parent / "opencode" / "go.jsonl").read_text().splitlines()
    ]
    menu = next(i for i, c in enumerate(go) if "Ctrl" in c or "to select" in c) + 1
    play(go[:menu])
    sys.stdin.readline()
    key = next(i for i, c in enumerate(go) if "API key" in c) + 1
    play(go[menu:key])
    sys.stdin.readline()
    play(go[key:])
    sys.exit(0)

good = chunks("setup-token.jsonl")
cut = next(i for i, c in enumerate(good) if "Paste" in c) + 1
play(good[:cut])
line = sys.stdin.readline().strip()
if line == "FAKECODE":
    play(good[cut:])
    sys.exit(0)
bad = chunks("setup-token-bad-code.jsonl")
play(bad[next(i for i, c in enumerate(bad) if "Paste" in c) + 1 :])
sys.exit(1)
