"""What a Codex child is started with, and its catalog. No I/O here.

The child reads ``~/.codex/config.toml`` (the person's login, providers and
skills) and aegis adds its own settings with ``-c``, which override the file.
Measured on codex-cli 0.162.1 (spec ``2026-10-09-aegis-2-codex-harness-design.md``):

- The token travels in ``AEGIS_SESSION_TOKEN``, named by ``env_http_headers``,
  because argv is readable by every user through ``ps``. Codex's default shell
  policy hands that variable to the model's shell, so it is excluded there.
- Under ``approval_policy="never"`` an MCP call fails unless its server says
  ``default_tools_approval_mode="approve"``.
- ``-c`` sets one key at a time and merges with the person's file, so a
  ``[mcp_servers.aegis]`` table of their own (a stdio command, ``enabled =
  false``) broke or disabled aegis's server. aegis's server has a name nobody
  else uses (``SERVER``), and the token's exclusion is added to the person's
  own ``exclude`` list, which ``-c`` would otherwise replace.
- The plugin features clone a marketplace on every start, and that ``git
  fetch`` outlived the server and held the thread's writer lease.
- aegis has no approval prompt, so nothing asks: the four permissions are
  sandbox policies, each allowing strictly more than the one before, so a
  session an agent spawns still has at most the agent's power.
- Under the owner's ``knowledge: native`` (setup.py) Codex starts without
  its own skills and memories (``NATIVE``): read from the config schema of
  0.163.0 and 0.164.0, ``skills.include_instructions`` drops the skills
  catalog from every turn, ``skills.bundled.enabled`` the skills Codex
  ships, and the ``memories`` feature (off by default) both reading and
  writing memories.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..claude.control import Catalog, Model, _doc
from ..mcp_servers import ref_name

if TYPE_CHECKING:
    from ..policy import McpServer

TOKEN_ENV = "AEGIS_SESSION_TOKEN"
SERVER = "aegis_session"
DISABLED = ("plugins", "remote_plugin")
PERMISSIONS = ("read", "write", "auto", "full")
SANDBOX_MODE = {
    "read": "read-only",
    "write": "workspace-write",
    "auto": "workspace-write",
    "full": "danger-full-access",
}
SANDBOX_POLICY: dict[str, dict[str, Any]] = {
    "read": {"type": "readOnly", "networkAccess": False},
    "write": {"type": "workspaceWrite", "networkAccess": False},
    "auto": {"type": "workspaceWrite", "networkAccess": True},
    "full": {"type": "dangerFullAccess"},
}
NATIVE_DISABLED = ("memories",)
NATIVE = (
    "skills.include_instructions=false",
    "skills.bundled.enabled=false",
    "memories.use_memories=false",
    "memories.generate_memories=false",
)
BUILTIN = (
    ("compact", "Summarize the conversation to free context."),
    ("review", "Review the uncommitted changes, or what the arguments ask."),
)


def split_model(model: str) -> tuple[str, str]:
    """``provider/model`` as (provider, model); the model keeps its slashes."""
    provider, _, model_id = model.partition("/")
    return provider, model_id


def _server_vars(s: McpServer) -> dict[str, str]:
    """The variables of Codex's environment that hold ``s``'s values. Codex
    forwards a stdio server's ``env_vars`` under their own names, so those
    keep their keys; a header goes through its reference (mcp_servers.by_ref)."""
    if s.transport == "stdio":
        return dict(s.env)
    return {ref_name(s.name, k): v for k, v in s.headers.items()}


def argv(
    bin: str,
    mcp_url: str | None,
    exclude: Sequence[str] = (),
    native: bool = False,
    servers: Sequence[McpServer] = (),
) -> list[str]:
    """``exclude`` is the person's own shell exclude list, kept. ``native``
    turns Codex's own skills and memories off. ``servers`` (#960) are written
    by variable name only; child_env sets the values."""
    out = [bin, "app-server"]
    for feature in (*DISABLED, *(NATIVE_DISABLED if native else ())):
        out += ["--disable", feature]
    if native:
        for kv in NATIVE:
            out += ["-c", kv]
    hidden = list(exclude)
    if mcp_url is not None:
        from ..mcp import HEADER

        hidden.append(TOKEN_ENV)
        out += [
            "-c", f"mcp_servers.{SERVER}.url={json.dumps(mcp_url)}",
            "-c", f'mcp_servers.{SERVER}.env_http_headers={{"{HEADER}"="{TOKEN_ENV}"}}',
            "-c", f'mcp_servers.{SERVER}.default_tools_approval_mode="approve"',
        ]  # fmt: skip
    for s in servers:
        key = f"mcp_servers.{s.name}"
        names = list(_server_vars(s))
        hidden += names
        if s.transport == "stdio":
            out += [
                "-c", f"{key}.command={json.dumps(s.command)}",
                "-c", f"{key}.args={json.dumps(list(s.args))}",
                "-c", f"{key}.env_vars={json.dumps(names)}",
            ]  # fmt: skip
        else:
            headers = ",".join(
                f"{json.dumps(k)}={json.dumps(ref_name(s.name, k))}" for k in s.headers
            )
            out += [
                "-c", f"{key}.url={json.dumps(s.url)}",
                "-c", f"{key}.env_http_headers={{{headers}}}",
            ]  # fmt: skip
        out += ["-c", f'{key}.default_tools_approval_mode="approve"']
    if mcp_url is not None or servers:
        hidden = [*dict.fromkeys(hidden)]
        out += ["-c", f"shell_environment_policy.exclude={json.dumps(hidden)}"]
    return out + ["-c", 'approval_policy="never"']


def child_env(
    base: Mapping[str, str],
    mcp: tuple[str, str] | None,
    servers: Sequence[McpServer] = (),
) -> dict[str, str]:
    env = {k: v for k, v in base.items() if k != TOKEN_ENV}
    if mcp is not None:
        env[TOKEN_ENV] = mcp[1]
    for s in servers:
        env |= _server_vars(s)
    return env


def provider_model(provider: str, m: dict) -> Model | None:
    """One entry of an OpenAI-compatible ``/models`` list (OpenRouter's has
    ``context_length``, ``pricing`` and ``supported_parameters``)."""
    mid = m.get("id")
    if not isinstance(mid, str) or not mid:
        return None
    value = f"{provider}/{mid}"
    window = m.get("context_length")
    raw = m.get("pricing")
    pricing: dict = raw if isinstance(raw, dict) else {}
    params = m.get("supported_parameters") or []
    return Model(
        value=value,
        resolved=value,
        label=str(m.get("name") or mid),
        doc="",
        efforts=(),
        window=window if isinstance(window, int) else None,
        free=pricing.get("prompt") == "0"
        and pricing.get("completion") == "0"
        and "tools" in params,
    )


def catalog_from(
    model_list: list, skills: list, listed: list[Model], current: str
) -> Catalog:
    """Codex's own models (``model/list``, the ``openai`` provider), the
    current provider's ``/models``, and the session's model if neither has it,
    so ``/model`` can always switch back."""
    commands = [
        {"name": name, "hint": "", "doc": doc, "source": "codex"}
        for name, doc in BUILTIN
    ]
    for s in skills:
        if isinstance(s, dict) and s.get("name"):
            commands.append(
                {
                    "name": str(s["name"]),
                    "hint": "",
                    "doc": _doc(str(s.get("description") or "")),
                    "source": "skill",
                }
            )
    models: list[Model] = []
    for m in model_list:
        if not isinstance(m, dict) or m.get("hidden") or not m.get("id"):
            continue
        value = f"openai/{m['id']}"
        efforts = tuple(
            str(o["reasoningEffort"])
            for o in m.get("supportedReasoningEfforts") or []
            if isinstance(o, dict) and o.get("reasoningEffort")
        )
        models.append(
            Model(
                value=value,
                resolved=value,
                label=str(m.get("displayName") or m["id"]),
                doc="",
                efforts=efforts,
            )
        )
    models += listed
    if current and not any(m.value == current for m in models):
        models.append(
            Model(value=current, resolved=current, label=current, doc="", efforts=())
        )
    return Catalog(tuple(commands), tuple(models))
