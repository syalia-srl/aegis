"""The notice sources a running aegis knows, and each one's defaults.

A per-session notice source (notices.py, ``Notices.set``) declares here the
keys it reads from its entry under ``notices:`` in .aegis.yaml, so config.py
can check that block without naming a source. A module declares its source
when it is imported; a plugin will declare its own through the plugin host.

This module imports nothing from aegis: config.py imports queues.py, so a
registry in config.py or notices.py would make a source import back into it.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass

_SOURCES: dict[str, dict] = {}
# Sources whose items keep their text whole, line breaks included, instead of
# the one line of TEXT_MAX notices.py cuts every other item to.
_UNCUT: set[str] = set()


@dataclass(frozen=True)
class Item:
    """One thing a source says about a session (``Notices.set``). ``key`` is
    stable for the condition (``m5:stalled``), so a text that changes every
    minute is told once. ``clear`` is what to say when it ends; empty ends it
    silently. ``wake`` delivers it at once to an idle owner, starting a turn."""

    key: str
    level: str  # "info" | "warn" | "critical"
    text: str
    clear: str = ""
    wake: bool = False


def declare(name: str, defaults: dict, *, cut: bool = True) -> None:
    """Declare ``name`` with its keys and their defaults. A bool default
    takes a bool, a number default a number above 0 (an int an int). An
    empty dict declares a source that is only on or off. ``cut`` False keeps
    its items' text whole: a core source whose text aegis builds from facts
    and must not lose its end."""
    _SOURCES[name] = dict(defaults)
    if not cut:
        _UNCUT.add(name)


def cuts(name: str) -> bool:
    """Whether ``name``'s item texts are cut to one short line."""
    return name not in _UNCUT


def declared() -> dict[str, dict]:
    return copy.deepcopy(_SOURCES)


# The core's own source: every info item makes one status block (notices.py).
# Declared here, so config.py knows it without importing notices.
declare("status", {"lines": 5})
