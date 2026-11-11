"""Named groups (#1039): one small JSON file per server, next to the metas
store, holding one record per group: ``{key, name, owner, created_at}``.

A group has no head. A session belongs to it through ``spec.group``, which
holds its key; the record exists while it has an open member. The key is
``grp-`` and 8 hex characters. A generated name is an adjective and a
laureate from the handle word lists (names.py); two of one owner's groups
never share a name, the later one taking ``-2``, ``-3``... Writes go through
write-then-rename, so a crash leaves the old file or the new one.
"""

from __future__ import annotations

import json
import logging
import os
import random
import secrets
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .names import ADJECTIVES_BY_LETTER, LAUREATES
from .ops import OpError

log = logging.getLogger(__name__)

KEY = "grp-"
NAME_MAX = 60
_ADJECTIVES = tuple(a for words in ADJECTIVES_BY_LETTER.values() for a in words)


@dataclass(frozen=True)
class Group:
    """One group's record."""

    key: str  # "grp-3fa9c210"
    name: str
    owner: str | None
    created_at: float


def generated_name(rng: random.Random | None = None) -> str:
    """An adjective and a laureate, such as ``brisk-backus``."""
    r = rng or random
    return f"{r.choice(_ADJECTIVES)}-{r.choice(LAUREATES)}"


class GroupStore:
    """``groups.json``: the records of one server's groups."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._groups: dict[str, Group] = {}
        self._load()

    def get(self, key: str) -> Group | None:
        return self._groups.get(key)

    def by_name(self, owner: str | None, name: str) -> Group | None:
        return next(
            (g for g in self._groups.values() if g.owner == owner and g.name == name),
            None,
        )

    def create(self, owner: str | None, name: str | None = None) -> Group:
        """A new record, with ``name`` when given and a generated one otherwise;
        a name its owner already uses gets a numeric suffix."""
        name = generated_name() if name is None else self._clean(name)
        g = Group(
            key=self._mint_key(),
            name=self._free_name(owner, name),
            owner=owner,
            created_at=time.time(),
        )
        self._groups[g.key] = g
        self._save()
        return g

    def rename(self, key: str, name: str) -> Group:
        """Rename a group; ``bad_name`` for an empty, multi-line or too long
        name, and the suffix for one its owner already uses."""
        g = self._groups.get(key)
        if g is None:
            raise OpError("no_group", f"no group {key!r}")
        name = self._free_name(g.owner, self._clean(name), except_key=key)
        g = Group(key=g.key, name=name, owner=g.owner, created_at=g.created_at)
        self._groups[key] = g
        self._save()
        return g

    def delete(self, key: str) -> None:
        if self._groups.pop(key, None) is not None:
            self._save()

    def keys(self) -> set[str]:
        return set(self._groups)

    def _clean(self, name: str) -> str:
        """A name is one trimmed line of at most ``NAME_MAX`` characters."""
        name = name.strip()
        if not name or "\n" in name or "\r" in name or len(name) > NAME_MAX:
            raise OpError(
                "bad_name",
                f"a group's name is one line of at most {NAME_MAX} characters",
            )
        return name

    def _free_name(
        self, owner: str | None, name: str, except_key: str | None = None
    ) -> str:
        """``name``, or ``-2``, ``-3``... when one of ``owner``'s other groups
        holds it, trimmed so the suffix keeps it within ``NAME_MAX``."""
        taken = {
            g.name
            for g in self._groups.values()
            if g.owner == owner and g.key != except_key
        }
        if name not in taken:
            return name
        n = 2
        while True:
            suffix = f"-{n}"
            candidate = name[: NAME_MAX - len(suffix)].rstrip() + suffix
            if candidate not in taken:
                return candidate
            n += 1

    def _mint_key(self) -> str:
        while (key := KEY + secrets.token_hex(4)) in self._groups:
            pass
        return key

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            log.warning("%s does not parse, keeping no groups: %s", self.path, e)
            return
        for e in data.get("groups", []) if isinstance(data, dict) else []:
            if not isinstance(e, dict):
                continue
            key, name = e.get("key"), e.get("name")
            owner, created = e.get("owner"), e.get("created_at")
            if not isinstance(key, str) or not isinstance(name, str):
                continue
            if owner is not None and not isinstance(owner, str):
                continue
            try:
                created_at = float(created)
            except (TypeError, ValueError):
                created_at = time.time()
            self._groups[key] = Group(
                key=key, name=name, owner=owner, created_at=created_at
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(
                {"groups": [asdict(g) for g in self._groups.values()]},
                ensure_ascii=False,
                indent=2,
            )
        )
        os.replace(tmp, self.path)
