"""pi behind the harness interface: ``pi --mode rpc`` over stdio."""

from __future__ import annotations

from pathlib import Path

from ..claude.control import Catalog
from ..harness import Launch
from .config import SERVER
from .process import PiProcess, probe
from .stream import LABEL


class Pi:
    name = "pi"
    src = "pi"
    label = LABEL
    steers_mid_turn = True  # a mid-turn message goes as pi's own `steer`
    # Notices still ride on aegis tool results until a run with a real model
    # shows how pi's steer reads one.
    steers_notices = False
    mcp_transports = frozenset({"http", "stdio"})
    # pi names MCP tools as Claude Code does (measured: mcp__aegis__meta).
    tool_prefix = f"mcp__{SERVER}__"

    def __init__(self, bin: str) -> None:
        self.bin = bin

    def process(self, launch: Launch) -> PiProcess:
        return PiProcess(self.bin, launch)

    async def probe(self, spec, stderr_path: Path) -> Catalog:
        return await probe(self.bin, spec.cwd, stderr_path, spec.model)
