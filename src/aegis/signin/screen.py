"""A terminal screen read as rows of text, so recipes can match what a login shows."""

import pyte


class Screen:
    def __init__(self, cols: int, rows: int) -> None:
        self.cols = cols
        self._screen = pyte.Screen(cols, rows)
        self._stream = pyte.Stream(self._screen)

    def feed(self, text: str) -> None:
        self._stream.feed(text)

    def rows(self) -> list[str]:
        return [row.rstrip() for row in self._screen.display]

    def text(self) -> str:
        """The rows joined by newlines; a full-width row joins the next, so a wrapped URL reads whole."""
        out = []
        for raw in self._screen.display:
            out.append(raw.rstrip())
            out.append("" if len(raw.rstrip()) == self.cols else "\n")
        return "".join(out).rstrip("\n")
