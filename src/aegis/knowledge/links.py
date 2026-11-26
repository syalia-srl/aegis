"""Resolve wiki links and their reverse edges over one visible catalog."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Literal, TypedDict

from markdown_it import MarkdownIt
from markdown_it.rules_inline import StateInline

from .items import Item

_WIKILINK = re.compile(r"\[\[([^\[\]\r\n]+)\]\]")
_QUALIFIED = re.compile(r"^[0-9a-f]{64}:")


def _wikilink(state: StateInline, silent: bool) -> bool:
    match = _WIKILINK.match(state.src, state.pos, state.posMax)
    if match is None or not (target := match[1].strip()):
        return False
    if not silent:
        state.push("wikilink", "", 0).content = target
    state.pos = match.end()
    return True


_markdown = MarkdownIt("commonmark")
_markdown.inline.ruler.before("link", "wikilink", _wikilink)


def references(body: str) -> list[str]:
    """Read wiki link tokens in prose, in first-occurrence order."""
    if "[[" not in body:
        return []
    return list(
        dict.fromkeys(
            child.content
            for token in _markdown.parse(body)
            for child in token.children or ()
            if child.type == "wikilink"
        )
    )


class Destination(TypedDict):
    id: str
    title: str
    source_id: str
    relative_path: str


class Link(TypedDict):
    reference: str
    qualified: bool
    status: Literal["resolved", "ambiguous", "pending"]
    target_id: str | None
    candidates: list[Destination]


class Neighbors(TypedDict):
    links: list[Link]
    backlinks: list[Destination]
    neighbor_count: int


def _destination(item: Item) -> Destination:
    return {
        "id": item.id,
        "title": item.title,
        "source_id": item.source_id,
        "relative_path": item.relative_path,
    }


class LinkGraph:
    def __init__(self, items: tuple[Item, ...]) -> None:
        by_id = {item.id: item for item in items}
        by_name: dict[str, list[Item]] = defaultdict(list)
        by_source: dict[tuple[str, str], list[Item]] = defaultdict(list)
        for item in items:
            for label in sorted({item.title.casefold(), item.name.casefold()}):
                by_name[label].append(item)
                by_source[item.source_id, label].append(item)

        self._links: dict[str, list[Link]] = {}
        self._backlinks: dict[str, dict[str, Destination]] = defaultdict(dict)
        for item in items:
            links: list[Link] = []
            for reference in references(item.body):
                qualified = _QUALIFIED.match(reference) is not None
                if qualified:
                    match = by_id.get(reference)
                    matches = [match] if match is not None else []
                else:
                    label = reference.casefold()
                    matches = by_source.get((item.source_id, label)) or by_name.get(
                        label, []
                    )
                target_id = matches[0].id if len(matches) == 1 else None
                if target_id is not None:
                    self._backlinks[target_id][item.id] = _destination(item)
                links.append(
                    {
                        "reference": reference,
                        "qualified": qualified,
                        "status": "resolved"
                        if target_id
                        else "ambiguous"
                        if matches
                        else "pending",
                        "target_id": target_id,
                        "candidates": [_destination(m) for m in matches]
                        if len(matches) > 1
                        else [],
                    }
                )
            self._links[item.id] = links

    def neighbors(self, entry_id: str) -> Neighbors:
        links = self._links[entry_id]
        incoming = self._backlinks.get(entry_id, {})
        outgoing = {link["target_id"] for link in links if link["target_id"]}
        return {
            "links": links,
            "backlinks": list(incoming.values()),
            "neighbor_count": len((outgoing | incoming.keys()) - {entry_id}),
        }
