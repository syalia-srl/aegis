"""MCP servers as configuration (#960): the ``mcp:`` map of ``.aegis.yaml``.

An entry is HTTP (``url``, optional ``headers``) or stdio (``command``,
optional ``args`` and ``env``); both or neither is an error on the entry,
never a guess. ``${VAR}`` in any value is read from aegis's environment when
a session starts (``expand``), so a token stays out of the file. A driver
that writes the servers into an argv or a file first calls ``by_ref``, which
swaps each header and env value for a reference to a variable of the child's
environment: an argv is readable by every user on the host, and a file
outlives the session.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import replace

from .ops import OpError
from .policy import McpServer

RESERVED = "aegis"
HTTP_KEYS = ("url", "headers")
STDIO_KEYS = ("command", "args", "env")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _strings(v: object) -> dict[str, str] | None:
    if not isinstance(v, dict):
        return None
    if any(isinstance(x, (dict, list)) or x is None for x in v.values()):
        return None
    return {str(k): str(x) for k, x in v.items()}


def _entry(name: str, entry: object) -> McpServer | str:
    """The server ``raw`` describes, or the error that stops it."""
    if name == RESERVED:
        return "the name aegis is reserved for aegis's own server"
    if not isinstance(entry, dict):
        return "an MCP server is a map with a url or a command"
    raw = {str(k): v for k, v in entry.items()}
    unknown = [str(k) for k in raw if k not in HTTP_KEYS + STDIO_KEYS]
    if unknown:
        return f"{', '.join(unknown)}: not an MCP server field"
    url, command = raw.get("url"), raw.get("command")
    if url and command:
        return "give a url or a command, not both"
    if not url and not command:
        return "an MCP server needs a url or a command"
    other = [k for k in (STDIO_KEYS if url else HTTP_KEYS) if k in raw]
    if other:
        kind = "a url" if url else "a command"
        return f"{', '.join(other)} do not go with {kind}"
    if url:
        headers = _strings(raw.get("headers") or {})
        if headers is None:
            return "headers must be a map of names to values"
        return McpServer.http(name, str(url), headers)
    args = raw.get("args") or []
    if not isinstance(args, list) or any(isinstance(a, (dict, list)) for a in args):
        return "args must be a list of strings"
    env = _strings(raw.get("env") or {})
    if env is None:
        return "env must be a map of names to values"
    return McpServer.stdio(name, str(command), [str(a) for a in args], env)


def parse(raw: object) -> tuple[dict[str, McpServer], dict[str, str]]:
    """The ``mcp:`` map as servers by name, and errors by name."""
    if raw is None:
        return {}, {}
    if not isinstance(raw, dict):
        return {}, {"mcp": "mcp must be a map of server names to servers"}
    servers: dict[str, McpServer] = {}
    errors: dict[str, str] = {}
    for name, entry in raw.items():
        got = _entry(str(name), entry)
        if isinstance(got, str):
            errors[str(name)] = got
        else:
            servers[str(name)] = got
    return servers, errors


def _sub(name: str, value: str, environ: Mapping[str, str]) -> str:
    def one(m: re.Match) -> str:
        if m.group(1) not in environ:
            raise OpError(
                "mcp_env",
                f"MCP server {name}: ${{{m.group(1)}}} is not set in aegis's "
                "environment",
            )
        return environ[m.group(1)]

    return _VAR.sub(one, value)


def expand(s: McpServer, environ: Mapping[str, str]) -> McpServer:
    """``s`` with every ``${VAR}`` read from ``environ``; an unset one refuses."""

    def sub(v: str) -> str:
        return _sub(s.name, v, environ)

    return replace(
        s,
        url=sub(s.url) if s.url is not None else None,
        headers={k: sub(v) for k, v in s.headers.items()},
        command=sub(s.command) if s.command is not None else None,
        args=tuple(sub(a) for a in s.args),
        env={k: sub(v) for k, v in s.env.items()},
    )


def ref_name(server: str, key: str) -> str:
    """The child's variable that holds ``server``'s header or env ``key``."""
    return "AEGIS_MCP_" + re.sub("[^A-Z0-9]", "_", f"{server}_{key}".upper())


def by_ref(
    servers: Sequence[McpServer],
) -> tuple[tuple[McpServer, ...], dict[str, str]]:
    """``servers`` with each header and env value replaced by
    ``${AEGIS_MCP_<NAME>_<KEY>}``, and the values those references name."""
    env: dict[str, str] = {}
    out = []
    for s in servers:
        refs = {}
        for field in ("headers", "env"):
            held = {}
            for k, v in getattr(s, field).items():
                env[ref_name(s.name, k)] = v
                held[k] = "${" + ref_name(s.name, k) + "}"
            refs[field] = held
        out.append(replace(s, **refs))
    return tuple(out), env
