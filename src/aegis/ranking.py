"""How good each model is and what it costs, from the vendored ``data/models.json``.

The scores are Artificial Analysis's Coding and Intelligence Indexes, and the
price is its list price in USD per million tokens. ``scripts/refresh-model-ranking``
rebuilds the packaged file before a release, and the ``model-ranking`` workflow
runs it every night and publishes the result on the ``model-ranking`` branch.
aegis never calls Artificial Analysis itself: a server downloads that nightly
file once a day into its state directory (``download``) and reads it in place
of the packaged one while it is valid and not older, so a new model is ranked
the day after Artificial Analysis scores it, with no release. A failed or
refused download keeps the last good one; an offline server reads the packaged
file. ``model_ranking: false`` in .aegis.yaml turns the download off.

The file is keyed by the model id an agent names (the catalog's ``value``).
A model is ranked when it has an Intelligence Index. Ranked models order by
Intelligence Index, higher first, the Coding Index breaking ties; unranked ones
go after every ranked one, in the order they were given. The Intelligence Index
orders because Artificial Analysis publishes it for every model it lists, while
many, the newest included, have no Coding Index.

``AEGIS_MODELS_FILE`` names a file read in place of both, so a test can serve
sample scores from a real ``aegis serve``. ``AEGIS_MODELS_URL`` points the
download elsewhere; empty, it never runs, which keeps the tests off the network.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Sequence
from typing import Any
from dataclasses import dataclass, replace
from functools import cache
from importlib import resources
from pathlib import Path

from .claude.control import Model


@dataclass(frozen=True)
class Score:
    aa: str  # the Artificial Analysis slug
    coding: float | None
    intelligence: float | None
    price_in: float | None
    price_out: float | None


@dataclass(frozen=True)
class Ranking:
    refreshed: str  # YYYY-MM-DD, or "" before the first refresh
    models: dict[str, Score]
    url: str = "https://artificialanalysis.ai"  # the attribution's link

    @classmethod
    def from_dict(cls, data: dict) -> Ranking:
        return cls(
            refreshed=data.get("refreshed") or "",
            url=data.get("url") or "https://artificialanalysis.ai",
            models={
                k: Score(
                    aa=v.get("aa", ""),
                    coding=v.get("coding"),
                    intelligence=v.get("intelligence"),
                    price_in=v.get("price_in"),
                    price_out=v.get("price_out"),
                )
                for k, v in (data.get("models") or {}).items()
            },
        )

    def score(self, model: str) -> Score | None:
        return self.models.get(model)

    def _key(self, model: str) -> tuple[float, float] | None:
        s = self.models.get(model)
        if s is None or s.intelligence is None:
            return None
        return (s.intelligence, s.coding if s.coding is not None else -1.0)

    def order(self, models: Sequence[str]) -> list[str]:
        """The models by Intelligence Index, higher first, the Coding Index
        breaking ties; those with no Intelligence Index last, as given."""
        ranked = [m for m in models if self._key(m) is not None]
        ranked.sort(key=lambda m: self._key(m) or (0.0, 0.0), reverse=True)
        return ranked + [m for m in models if self._key(m) is None]

    def rank(self, model: str, among: Sequence[str]) -> int | None:
        """The model's 1-based place in ``order(among)``, or None when it is
        not among them or has no Intelligence Index."""
        if model not in among or self._key(model) is None:
            return None
        return self.order(among).index(model) + 1


NIGHTLY_URL = (
    "https://raw.githubusercontent.com/syalia-srl/aegis/model-ranking/models.json"
)
FETCH_TIMEOUT_S = 30.0
REFRESH_S = 86400.0
RETRY_S = 3600.0

# The downloaded file, set by the server (``use``); None reads only the
# packaged one.
_downloaded: Path | None = None


def valid(data: Any) -> bool:
    """A ranking file aegis can read: a date and at least one scored model."""
    if not isinstance(data, dict) or not isinstance(data.get("refreshed"), str):
        return False
    models = data.get("models")
    return isinstance(models, dict) and any(
        isinstance(v, dict) and v.get("intelligence") is not None
        for v in models.values()
    )


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if valid(data) else None


def use(path: Path | None) -> None:
    """Read ``path``, the last good download, in place of the packaged file
    whenever it is valid and not older; None goes back to the packaged one."""
    global _downloaded
    if path != _downloaded:
        _downloaded = path
        ranking.cache_clear()


def download(path: Path, url: str | None = None) -> bool:
    """Fetch the nightly file into ``path`` when it is valid, atomically, and
    drop the cached ranking. False, and ``path`` untouched, on any failure:
    the network, a body that is not a ranking, or one older than ``path``."""
    url = url if url is not None else os.environ.get("AEGIS_MODELS_URL", NIGHTLY_URL)
    if not url:
        return False
    try:
        with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_S) as r:
            data = json.loads(r.read())
    except (OSError, ValueError):
        return False
    if not valid(data):
        return False
    have = _read(path)
    if have is not None and data["refreshed"] < have["refreshed"]:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)
    ranking.cache_clear()
    return True


@cache
def ranking() -> Ranking:
    if path := os.environ.get("AEGIS_MODELS_FILE"):
        return Ranking.from_dict(json.loads(Path(path).read_text()))
    data = json.loads(resources.files("aegis").joinpath("data/models.json").read_text())
    if _downloaded is not None:
        nightly = _read(_downloaded)
        if nightly is not None and nightly["refreshed"] >= data.get("refreshed", ""):
            data = nightly
    return Ranking.from_dict(data)


def annotate(models: Sequence[Model], r: Ranking | None = None) -> tuple[Model, ...]:
    """The models with their scores and rank among themselves, and the file's
    price where the harness gave none. Pass one harness's catalog: the rank
    is among the models given."""
    r = r or ranking()
    among = [m.value for m in models]
    out = []
    for m in models:
        s = r.score(m.value)
        if s is None:
            out.append(m)
            continue
        own = m.price_in is not None or m.price_out is not None
        out.append(
            replace(
                m,
                rank=r.rank(m.value, among),
                coding=s.coding,
                intelligence=s.intelligence,
                price_in=m.price_in if own else s.price_in,
                price_out=m.price_out if own else s.price_out,
            )
        )
    return tuple(out)
