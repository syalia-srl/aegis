"""Only an explicitly marked, completed top-level Bash fence requests execution."""

from dataclasses import dataclass

from markdown_it import MarkdownIt

MARKER = "# aegis: execute"
_markdown = MarkdownIt("commonmark")


class ProtocolError(ValueError):
    """A response requested ambiguous shell work; none of it may run."""


@dataclass(frozen=True)
class Request:
    code: str
    prose: str


def request(markdown: str) -> Request | None:
    lines = markdown.splitlines(keepends=True)
    marked = []
    for token in _markdown.parse(markdown):
        if token.type != "fence" or token.level or token.info.strip() != "bash":
            continue
        first = next((line for line in token.content.splitlines() if line.strip()), "")
        if first != MARKER or token.map is None:
            continue
        start, end = token.map
        closing = lines[end - 1].strip()
        if len(closing) < len(token.markup) or set(closing) != {token.markup[0]}:
            continue
        marked.append((token.content, start, end))
    if len(marked) > 1:
        raise ProtocolError("Return only one marked Bash block per response; none ran.")
    if not marked:
        return None
    code, start, end = marked[0]
    return Request(code, "".join(lines[:start] + lines[end:]).strip())
