"""A Claude Code child's environment, with or without a connection.

Without one, the child inherits the server's environment and the Claude
sign-in token (``login.with_token``), as every session always has. With one
(``session.Connection``, from an agent's ``base_url``), it runs against that
endpoint instead: the Claude credentials it would inherit are removed, the
endpoint and its key are added, then the agent's ``env``. Whatever aegis sets
for the harness afterwards (the session variables, MCP refs, the memory
switch, an isolated config dir) goes on top, so a name aegis sets still wins.

The endpoint depends on Claude Code sending ``x-claude-code-session-id`` on
every request (the Go gateway answers 400 MissingSessionID without it); aegis
neither sets that header nor exposes it.

The key is read here, at each process start, like the Claude token: the
agent's ``api_key_file`` when it names one, checked as ``login`` checks its
own token file, else the pool's own key (``QuotaProvider.inference_key``).
Spec: docs/superpowers/specs/2026-11-21-claude-code-on-another-account-design.html.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from . import login

if TYPE_CHECKING:
    from ..session import Connection

# What a child would inherit that points it at Anthropic or signs it in there.
INHERITED = (
    login.VAR,
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_BASE_URL",
)


class KeyProblem(Exception):
    """Why a connection has no usable key, in words for the person."""


def key_problem(api_key_file: str | None, pool: str | None) -> str | None:
    """Why the key cannot be read, or None when it can."""
    try:
        read_key(api_key_file, pool)
    except KeyProblem as e:
        return str(e)
    return None


def read_key(api_key_file: str | None, pool: str | None) -> str:
    """The connection's key: its file when given, else its pool's own."""
    if api_key_file:
        path = Path(api_key_file).expanduser()
        if not path.is_file():
            raise KeyProblem(f"{path} does not exist")
        if why := login.file_problem(path):
            raise KeyProblem(why)
        try:
            key = path.read_text().strip()
        except OSError as e:
            raise KeyProblem(f"{path}: {e.strerror}") from e
        if not key:
            raise KeyProblem(f"{path} is empty")
        return key
    from ..quota import PROVIDERS

    provider = next((p for p in PROVIDERS if p.name == pool), None)
    if provider is None or provider.inference_key is None:
        raise KeyProblem(f"no key: pool {pool} has none of its own; set api_key_file")
    key = provider.inference_key()
    if not key:
        raise KeyProblem(
            f"no key: {provider.label} holds no key here; sign in or set api_key_file"
        )
    return key


def base_env(
    connection: "Connection", pool: str | None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """``base`` (the server's own when None) pointed at the connection: the
    Claude credentials removed, the endpoint, its key and the agent's ``env``
    added. Raises KeyProblem when there is no usable key."""
    key = read_key(connection.api_key_file, pool)
    env = {
        k: v
        for k, v in (os.environ if base is None else base).items()
        if k not in INHERITED
    }
    env["ANTHROPIC_BASE_URL"] = connection.base_url
    env["ANTHROPIC_AUTH_TOKEN"] = key
    env.update(connection.env)
    return env
