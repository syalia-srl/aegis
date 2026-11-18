"""Who may use this server: the holder of its one token.

The token in ``<state>/token`` is the admin's, named after the server's owner
(the Unix user ``aegis serve`` runs as), with mode 0600 and reused across
restarts so an open tab survives one. A hand edit of the file applies at the
next call. Revoking it writes a new one, so a server is never left without a
token.

aegis 3.0 removed the multi-user mode (#958), which kept more tokens, with
roles, in ``<state>/tokens.json``. A state directory that still holds one of
them refuses to start (``refuse_old_state``) rather than run with them
ignored: their holders would lose access with nothing saying why.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
from pathlib import Path

from .ops import OpError, Principal

FILE = "tokens.json"


class OldStateError(Exception):
    """The state directory holds tokens from the removed multi-user mode."""


def refuse_old_state(state_root: Path) -> None:
    """Raise OldStateError when ``<state>/tokens.json`` lists any token. A
    missing file, an empty object and an empty list are fine."""
    path = state_root / FILE
    try:
        doc = json.loads(path.read_text())
    except FileNotFoundError:
        return
    except (OSError, ValueError):
        doc = {"tokens": ["unreadable"]}
    if isinstance(doc, dict) and not doc.get("tokens"):
        return
    raise OldStateError(
        f"{path} holds tokens from aegis's multi-user mode, which aegis 3.0 "
        "removed. Delete the file to run with the admin token alone."
    )


def load_or_create_token(state_root: Path) -> str:
    """The admin's token, kept in ``<state>/token`` with mode 0600."""
    path = state_root / "token"
    if path.exists():
        return path.read_text().strip()
    state_root.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token + "\n")
    return token


def _write_private(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    tmp.replace(path)


class TokenStore:
    def __init__(self, state_root: Path, owner: str) -> None:
        self.root = state_root
        self.owner = owner
        self._admin = load_or_create_token(state_root)
        self._admin_mtime = self._mtime(state_root / "token")

    @staticmethod
    def _mtime(p: Path) -> int | None:
        try:
            return p.stat().st_mtime_ns
        except OSError:
            return None

    def _fresh(self) -> None:
        m = self._mtime(self.root / "token")
        if m != self._admin_mtime:
            self._admin = load_or_create_token(self.root)
            self._admin_mtime = m

    def resolve(self, token: object) -> Principal | None:
        """The owner when ``token`` is the server's, else None."""
        if not isinstance(token, str) or not token:
            return None
        self._fresh()
        if hmac.compare_digest(token, self._admin):
            return Principal(self.owner)
        return None

    def admin_token(self) -> str:
        self._fresh()
        return self._admin

    def use_admin(self, token: str) -> None:
        """Make ``token`` the admin's: what the web layer was built with. A
        no-op when it already is, which is every real start."""
        self._fresh()
        if token and token != self._admin:
            _write_private(self.root / "token", token + "\n")
            self._admin, self._admin_mtime = token, self._mtime(self.root / "token")

    def revoke(self, name: str) -> str:
        """Replace the owner's token and return the new one, so whoever
        revoked it can sign in with it."""
        if name != self.owner:
            raise OpError("unknown_token", f"no token named {name}")
        token = secrets.token_urlsafe(32)
        _write_private(self.root / "token", token + "\n")
        self._admin, self._admin_mtime = token, self._mtime(self.root / "token")
        return token
