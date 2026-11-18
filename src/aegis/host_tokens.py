"""Tokens a host mints for its callers (``Server.token_for``, #959).

Signed and stateless: ``aeg1.<payload>.<signature>``, the payload the caller's
key and an expiry, the signature HMAC-SHA256 under a secret kept in
``<state>/host-secret`` (mode 0600). Nothing is stored per token, and a token
stays valid across a restart until it expires.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

PREFIX = "aeg1."
FILE = "host-secret"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(secret: bytes, payload: str) -> str:
    return _b64(hmac.new(secret, payload.encode(), hashlib.sha256).digest())


def mint(secret: bytes, key: str, ttl: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    payload = _b64(json.dumps({"k": key, "exp": int(now + ttl)}).encode())
    return f"{PREFIX}{payload}.{_sign(secret, payload)}"


def verify(secret: bytes, token: str, now: float | None = None) -> str | None:
    """The key a token was minted for, or None for a bad shape, a bad
    signature or an expired token."""
    if not token.startswith(PREFIX):
        return None
    payload, _, sig = token.removeprefix(PREFIX).partition(".")
    if not sig or not hmac.compare_digest(sig, _sign(secret, payload)):
        return None
    try:
        doc = json.loads(_unb64(payload))
        key, exp = doc["k"], doc["exp"]
    except (ValueError, KeyError, TypeError):
        return None
    now = time.time() if now is None else now
    if not isinstance(key, str) or not isinstance(exp, int) or now > exp:
        return None
    return key


def load_or_create_secret(state_root: Path) -> bytes:
    path = state_root / FILE
    try:
        return path.read_bytes()
    except FileNotFoundError:
        pass
    state_root.mkdir(parents=True, exist_ok=True)
    secret = secrets.token_bytes(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_bytes()
    with os.fdopen(fd, "wb") as f:
        f.write(secret)
    return secret
