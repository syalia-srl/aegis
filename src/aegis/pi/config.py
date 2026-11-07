"""What a pi child is started with, and its catalog. No I/O but the agent
directory, which ``agent_dir`` writes.

Measured on pi 1.5.0 (``@earendil-works/pi-coding-agent``; spec
``2026-11-05-pi-harness-design.html``; fixture ``tests/fixtures/pi/rpc-turn.jsonl``):

- The child is ``pi --mode rpc``: JSONL commands on stdin, JSONL events and
  ``{type: "response"}`` answers on stdout, one process per session.
- pi reads MCP servers only from ``<agent dir>/mcp.json`` (or a project's
  ``.pi/mcp.json``, behind project trust). aegis points ``PI_CODING_AGENT_DIR``
  at a directory of its own, whose ``mcp.json`` names aegis's URL and takes
  the token from the environment (pi expands ``${VAR}`` in header values, not
  in ``url``: measured, ``url must be an http or https URL``), so one file
  serves every session of a server and holds no secret. The person's provider keys, settings and custom
  models are linked in from ``~/.pi/agent``.
- MCP tools are named ``mcp__<server>__<tool>``, Claude Code's names.
- pi asks before nothing. ``--tools`` is an allowlist; an entry that starts
  with ``mcp__`` makes it filter MCP tools as well, and a deferred MCP tool
  loads only through ``tool_search``, so every list names both.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ..claude.control import Catalog, Model
from ..harness import HEADER, TOKEN_VAR, Launch
from ..mcp_servers import by_ref

if TYPE_CHECKING:
    from ..policy import McpServer

SERVER = "aegis"
AGENT_DIR_VAR = "PI_CODING_AGENT_DIR"
# pi's thinking levels, lowest first (``--thinking``, ``set_thinking_level``).
EFFORTS = ("off", "minimal", "low", "medium", "high", "xhigh")
_AEGIS = ("tool_search", f"mcp__{SERVER}__*")
# aegis's level -> pi's tool allowlist; None runs every tool. pi has no path
# fence, so auto is full.
TOOLS: dict[str, tuple[str, ...] | None] = {
    "read": ("read", "grep", "find", "ls", *_AEGIS),
    "write": ("read", "grep", "find", "ls", "edit", "write", *_AEGIS),
    "auto": None,
    "full": None,
}
# The tools the model sees without a search: the system prompt asks for them
# on every turn.
DIRECT = ("meta", "turn_end", "plan_update")
# What the person's own agent directory lends aegis's.
LINKED = ("auth.json", "settings.json", "models.json")
# pi's built-in tools, and the row each becomes.
TOOL_NAMES = {
    "read": "Read",
    "bash": "Bash",
    "edit": "Edit",
    "write": "Write",
    "grep": "Grep",
    "find": "Glob",
    "ls": "LS",
}


def state_dir(launch: Launch) -> Path:
    """aegis's own pi state, beside the session logs (registry._stderr_path)."""
    return launch.stderr_path.parent.parent / "pi"


def mcp_config(url: str | None, servers: Sequence[McpServer] = ()) -> dict:
    """``servers`` (#960) go in by reference (mcp_servers.by_ref): pi expands
    ``${VAR}`` in header and env values, and the file is shared."""
    entries: dict[str, dict] = {}
    if url is not None:
        entries[SERVER] = {
            "url": url,
            "headers": {HEADER: "${" + TOKEN_VAR + "}"},
            "exposure": "deferred",
            "toolExposure": {name: "direct" for name in DIRECT},
        }
    for s in by_ref(servers)[0]:
        if s.transport == "http":
            entries[s.name] = {"url": s.url, "headers": s.headers}
        else:
            entries[s.name] = {"command": s.command, "args": list(s.args), "env": s.env}
    return {"mcpServers": entries}


def agent_dir(
    root: Path,
    url: str | None,
    home: Path | None = None,
    servers: Sequence[McpServer] = (),
) -> Path:
    """aegis's agent directory under ``root``, written if missing or stale:
    the aegis ``mcp.json`` (left as it is when there is no endpoint: a probe
    runs with ``--no-mcp``) and links to what the person's own holds. pi reads
    servers only from this file, so a session with ``servers`` gets
    ``agent-<hash of the file>``: sessions with one set share a directory."""
    text = json.dumps(mcp_config(url, servers), indent=2) + "\n"
    name = "agent"
    if servers:
        name += "-" + hashlib.sha256(text.encode()).hexdigest()[:8]
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    mcp = d / "mcp.json"
    if url is not None or servers:
        if not mcp.exists() or mcp.read_text() != text:
            mcp.write_text(text)
    own = (home or Path.home()) / ".pi" / "agent"
    for name in LINKED:
        link, target = d / name, own / name
        if target.exists() and not link.exists() and not link.is_symlink():
            link.symlink_to(target)
    return d


def argv(
    bin: str, launch: Launch, session_dir: Path | None, session_file: str | None
) -> list[str]:
    cmd = [bin, "--mode", "rpc"]
    cmd += ["--session-dir", str(session_dir)] if session_dir else ["--no-session"]
    if launch.model:
        cmd += ["--model", launch.model]
    if launch.effort in EFFORTS:
        cmd += ["--thinking", launch.effort]
    if session_file:
        cmd += ["--session", session_file]
    if launch.system_prompt:
        cmd += ["--append-system-prompt", launch.system_prompt]
    tools = TOOLS.get(launch.permission)
    if tools is not None:
        extra = tuple(f"mcp__{s.name}__*" for s in launch.extra_mcp)
        cmd += ["--tools", ",".join((*tools, *extra))]
    if launch.knowledge_native:
        cmd.append("--no-skills")
    if launch.mcp is None and not launch.extra_mcp:
        cmd.append("--no-mcp")
    return cmd


def split_model(value: str) -> tuple[str, str]:
    """``provider/id`` as pi's ``set_model`` takes it; an id alone keeps the
    provider empty, which pi refuses with its own message."""
    provider, sep, model = value.partition("/")
    return (provider, model) if sep else ("", value)


def models_from(entries: list, current: str = "") -> tuple[Model, ...]:
    """``get_available_models`` as the catalog: one model per entry, named
    ``provider/id`` (what ``--model`` and ``.aegis.yaml`` take)."""
    models: list[Model] = []
    for m in entries:
        if not isinstance(m, dict) or not m.get("id") or not m.get("provider"):
            continue
        value = f"{m['provider']}/{m['id']}"
        window = m.get("contextWindow")
        models.append(
            Model(
                value=value,
                resolved=value,
                label=str(m.get("name") or m["id"]),
                doc="",
                efforts=EFFORTS if m.get("reasoning") else (),
                window=window if isinstance(window, int) else None,
            )
        )
    if current and not any(m.value == current for m in models):
        models.append(
            Model(value=current, resolved=current, label=current, doc="", efforts=())
        )
    return tuple(models)


def catalog_from(models: tuple[Model, ...]) -> Catalog:
    return Catalog((), models)


def tool_name(name: str, args: dict) -> tuple[str, dict]:
    """The aegis tool row a pi call becomes, as ``(name, input)``: a file
    tool's ``path`` becomes the ``file_path`` every view in
    ``transcript/describe.py`` reads, and an edit's first block its
    ``old_string`` / ``new_string``."""
    row = TOOL_NAMES.get(name, name)
    inp = dict(args)
    if "path" in inp and "file_path" not in inp:
        inp["file_path"] = inp["path"]
    edits = inp.get("edits")
    if (
        row == "Edit"
        and isinstance(edits, list)
        and edits
        and isinstance(edits[0], dict)
    ):
        inp.setdefault("old_string", str(edits[0].get("oldText") or ""))
        inp.setdefault("new_string", str(edits[0].get("newText") or ""))
    return row, inp
