"""Keep tokens out of a login's output, even when one arrives split across reads."""

import re

DEFAULT_SHAPES = [
    re.compile(r"sk-ant-oat01-[A-Za-z0-9_-]{20,400}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
]
MASK = "••••"
_CARRY_MAX = 420
_TOKEN_CHAR = re.compile(r"[A-Za-z0-9_-]")


class Masker:
    def __init__(self, shapes: list[re.Pattern]) -> None:
        self.shapes = shapes
        self._carry = ""

    def _mask(self, text: str) -> str:
        for shape in self.shapes:
            text = shape.sub(MASK, text)
        return text

    def _hold_from(self, text: str) -> int:
        """Where the tail that could still grow into a token starts, or len(text)."""
        i = text.rfind("sk-")
        if (
            i >= 0
            and len(text) - i <= _CARRY_MAX
            and all(_TOKEN_CHAR.match(c) for c in text[i:])
        ):
            return i
        for end in ("sk", "s"):
            if text.endswith(end):
                return len(text) - len(end)
        return len(text)

    def feed(self, text: str) -> str:
        text = self._carry + text
        cut = self._hold_from(text)
        self._carry = text[cut:]
        return self._mask(text[:cut])

    def flush(self) -> str:
        out, self._carry = self._mask(self._carry), ""
        return out
