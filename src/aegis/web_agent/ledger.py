"""A session's private, atomic browser and command-delivery record.

Started without a saved result is uncertain. Observing a provider message again
must never turn that state back into prepared, even if its command is identical.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = (
            json.loads(path.read_text())
            if path.exists()
            else {"version": 1, "executions": {}}
        )
        if self.data.get("version") != 1 or not isinstance(
            self.data.get("executions"), dict
        ):
            raise ValueError(
                "Unsupported web session ledger; manual recovery is required"
            )

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = self.path.with_suffix(".tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(self.data, f, ensure_ascii=False)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def update(self, **values: object) -> None:
        self.data.update(values)
        self._save()

    def get(self, key: str) -> dict | None:
        return self.data["executions"].get(key)

    def prepare(self, key: str, code: str) -> dict:
        if old := self.get(key):
            if old["code"] != code:
                raise ValueError("The command in an observed message changed; none ran")
            return old
        record = {"code": code, "phase": "prepared"}
        self.data["executions"][key] = record
        self._save()
        return record

    def _transition(
        self, key: str, allowed: tuple[str, ...], phase: str, **extra: object
    ) -> None:
        record = self.data["executions"][key]
        if record["phase"] not in allowed:
            raise ValueError(
                f"Cannot transition execution from {record['phase']} to {phase}"
            )
        record.update(phase=phase, **extra)
        self._save()

    def start(self, key: str) -> None:
        self._transition(key, ("prepared",), "started")

    def complete(self, key: str, result: dict) -> None:
        self._transition(key, ("started",), "completed", result=result)

    def delivery_pending(self, key: str) -> None:
        self._transition(key, ("completed",), "delivery-pending")

    def delivered(self, key: str) -> None:
        self._transition(key, ("delivery-pending",), "delivered")

    def uncertain(self) -> list[str]:
        return [
            key for key, r in self.data["executions"].items() if r["phase"] == "started"
        ]

    def pending(self) -> list[str]:
        return [
            key
            for key, r in self.data["executions"].items()
            if r["phase"] in ("completed", "delivery-pending")
        ]
