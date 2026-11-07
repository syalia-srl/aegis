"""What happened, told to the host policy's on_event (#959).

Events wait in one queue and one task hands them to the hook in order, so a
slow or failing hook never holds up a turn; an exception it raises is logged
and dropped.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .policy import Event

if TYPE_CHECKING:
    from .policy import HostPolicy

log = logging.getLogger(__name__)


class EventPump:
    def __init__(self, policy: HostPolicy) -> None:
        self._policy = policy
        self._queue: asyncio.Queue[tuple[Any, Event]] | None = None
        self._task: asyncio.Task | None = None

    def emit(self, who: Any, event: Event) -> None:
        if self._queue is None:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return  # no loop, so no session either: nothing to tell
            self._queue = asyncio.Queue()
            self._task = asyncio.create_task(self._run())
        self._queue.put_nowait((who, event))

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            who, event = await self._queue.get()
            try:
                await self._policy.on_event(who, event)
            except Exception:
                log.exception("aegis: the host policy's on_event failed")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
