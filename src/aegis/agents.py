"""Agents: named presets read from the ``agents:`` map of ``.aegis.yaml``.

An agent fixes a harness, a model, an effort, a permission and, optionally, a
priming prompt and the harness tools it keeps although aegis replaces them
(``keep_harness_tools``). A spawn starts from one agent and may override every field
but the priming (``resolve``); one-off instructions belong in the first
message.

Nothing in the file is a default. An agent that omits a field, or sets one to
an empty string, is listed with an ``error`` and cannot be spawned, and the
other agents in the file are unaffected. A field the loader filled in would be
a setting nobody chose, with nothing to show it was filled.

``models:`` names an ordered list instead of one ``model:``, a cascade
(cascade.py); ``model`` is then its head, and ``model: x`` is a cascade of one.
Every model of a cascade runs on the agent's harness.

Three forms name the harness: flat ``harness:``, ``provider: <harness>`` as a
string (the Workspace's own form), and a nested ``provider:`` mapping whose
``name`` is the harness and whose other keys are fields.

A ``claude-code`` agent may name a connection: ``base_url``, the endpoint it
sends Claude Code to instead of Anthropic, with ``pool``, the account aegis
meters it on, and optionally ``api_key_file`` and ``env``. Each is refused,
with the agent's error, where it would put a credential into ``.aegis.yaml``
(which is in git) or let the process reach the person's own Claude sign-in.
The nested ``provider:`` map names models, not an account, so a connection
written inside it is refused.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ruamel.yaml import YAML, YAMLError

from .claude.process import PERMISSION_MODE, REPLACED_TOOLS
from .guards import AGENT_ACTIONS, Guard
from . import budgets, guards
from .ops import OpError
from .quota import PROVIDERS
from .quota_gate import NONE
from . import scopes
from .roots import CONFIG_FILE
from .scopes import Scope
from .session import SWITCHES, SpawnSpec

EFFORTS = ("low", "medium", "high", "xhigh", "max")
HARNESSES = ("claude-code", "opencode", "codex", "cursor", "pi")
SUPPORTED_HARNESSES = HARNESSES
FIELDS = ("harness", "model", "effort", "permission")
# Least to most: an agent spawns sessions with at most its own permission.
PERMISSION_ORDER = ("read", "write", "auto", "full")


OPENCODE_MODEL = (
    "an OpenCode model is provider/model, such as opencode-go/deepseek-v4-pro"
)

CODEX_MODEL = "a Codex model is provider/model, such as openai/gpt-5.5"

# A connection's keys (``_connection``), and the pools it may name: a quota
# provider's, or none. codex has no provider, so no gauge could read it.
CONNECTION_KEYS = ("base_url", "pool", "api_key_file", "env")
CONNECTION_POOLS = tuple(p.name for p in PROVIDERS) + (NONE,)
# env names that would carry a credential into a file in git, or point the
# process back at the person's own config directory and its sign-in.
ENV_REFUSED = (
    "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_BASE_URL", "CLAUDE_CONFIG_DIR", "HOME",
)  # fmt: skip
ENV_REFUSED_SUFFIXES = ("_TOKEN", "_KEY", "_SECRET")
_VAR = re.compile(r"\$\{")


def _model_error(harness: str, model: str) -> str | None:
    if harness == "opencode" and "/" not in model:
        return OPENCODE_MODEL
    if harness == "codex" and "/" not in model:
        return CODEX_MODEL
    return None


class ConfigError(Exception):
    """``.aegis.yaml`` exists but cannot be parsed."""


@dataclass(frozen=True)
class Agent:
    name: str
    harness: str
    model: str
    effort: str
    permission: str
    priming: str | None = None
    keep_harness_tools: tuple[str, ...] = ()
    # A linked server to exit through, or "off"; blank: the server's exit_via.
    exit: str = ""
    # The MCP servers the agent attaches, by name, from the mcp: map (#960).
    mcp: tuple[str, ...] = ()
    # Why this agent cannot be spawned as written; None when it can.
    error: str | None = None
    # The cascade, head first; (model,) for a one-model agent.
    models: tuple[str, ...] = ()
    # Models ``models:`` listed more than once; the repeats are dropped.
    repeated: tuple[str, ...] = ()
    # A product agent's switches (session.SWITCHES, #983).
    harness_tools: bool = True
    isolated: bool = False
    primer: bool = True
    notices: bool = True
    # What its sessions may use (scopes.py, #1066), and its tools: block as
    # the file writes it; the scope that allows everything without one.
    scope: Scope = scopes.ANY
    tools: dict = field(default_factory=dict)
    # Its quota guard (guards.py, #1067); None: unguarded.
    guard: Guard | None = None
    # Its spending caps (budgets.py, #1068); (): no cap.
    budget: tuple[budgets.Cap, ...] = ()
    # Its connection (claude-code only): the endpoint, the pool aegis meters
    # it on, the key file (~ expanded; "": the pool's own key) and the
    # variables added to its process. "" and {}: none, Anthropic as today.
    base_url: str = ""
    pool: str = ""
    api_key_file: str = ""
    env: dict[str, str] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return self.error is None and self.harness in SUPPORTED_HARNESSES

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "harness": self.harness,
            "model": self.model,
            "models": list(self.models),
            "effort": self.effort,
            "permission": self.permission,
            "enabled": self.enabled,
            "error": self.error,
            "has_priming": bool(self.priming),
            **({"tools": self.tools} if self.tools else {}),
        }


def _invalid(field: str, value: str) -> str | None:
    if field == "effort" and value not in EFFORTS:
        return f"effort {value!r} is not one of {', '.join(EFFORTS)}"
    if field == "permission" and value not in PERMISSION_MODE:
        return f"permission {value!r} is not one of {', '.join(PERMISSION_MODE)}"
    return None


def _agent(name: str, raw: Any) -> Agent:
    d: dict[str, Any] = dict(raw) if isinstance(raw, dict) else {}
    provider = d.pop("provider", None)
    nested_error = None
    if isinstance(provider, str):
        d["harness"] = provider
    elif isinstance(provider, dict):
        if inside := [k for k in CONNECTION_KEYS if k in provider]:
            nested_error = (
                f"{', '.join(inside)} go on the agent, not in provider:, which "
                "names models, not an account"
            )
        d |= {k: v for k, v in provider.items() if k != "name"}
        if provider.get("name"):
            d["harness"] = provider["name"]
    models, repeated, models_error = _models(d)
    if models:
        d["model"] = models[0]
    values = {f: "" if d.get(f) in (None, "") else str(d[f]) for f in FIELDS}
    missing = [f for f in FIELDS if not values[f]]
    if missing:
        verb = "is" if len(missing) == 1 else "are"
        error: str | None = f"{', '.join(missing)} {verb} missing"
    else:
        error = next((e for f in FIELDS if (e := _invalid(f, values[f]))), None)
        error = error or next(
            (e for m in models or (values["model"],)
             if (e := _model_error(values["harness"], m))),
            None,
        )  # fmt: skip
    error = models_error or error
    priming = d.get("priming")
    keep, keep_error = _keep(d.get("keep_harness_tools"))
    mcp = d.get("mcp")
    mcp_ok = mcp is None or (
        isinstance(mcp, list) and all(isinstance(n, str) and n for n in mcp)
    )
    mcp_error = None if mcp_ok else "mcp must be a list of MCP server names"
    scope, scope_error = scopes.parse(d.get("tools"))
    guard, guard_error = guards.parse(d.get("quota"), AGENT_ACTIONS)
    budget, budget_error = budgets.parse(d.get("budget"))
    conn, conn_error = _connection(d, values["harness"])
    if nested_error:
        conn = _connection({}, "")[0]
    switches = {k: d.get(k, default) for k, default in SWITCHES.items()}
    switch_error = next(
        (f"{k} must be true or false" for k, v in switches.items()
         if not isinstance(v, bool)),
        None,
    )  # fmt: skip
    return Agent(
        name=str(name),
        **values,
        priming=str(priming) if priming else None,
        keep_harness_tools=keep,
        exit=str(d.get("exit") or ""),
        mcp=tuple(mcp) if mcp_ok and mcp else (),
        error=error
        or keep_error
        or mcp_error
        or switch_error
        or scope_error
        or guard_error
        or budget_error
        or nested_error
        or conn_error,
        models=models or ((values["model"],) if values["model"] else ()),
        repeated=repeated,
        harness_tools=_flag(switches, "harness_tools"),
        isolated=_flag(switches, "isolated"),
        primer=_flag(switches, "primer"),
        notices=_flag(switches, "notices"),
        scope=scope,
        tools=scopes.form(d.get("tools")),
        guard=guard,
        budget=budget,
        **conn,
    )


def _connection(d: dict, harness: str) -> tuple[dict[str, Any], str | None]:
    """An agent's connection fields, and the first error that stops it; the
    fields are empty when there is an error, so a refused connection never
    reaches a spec."""
    empty: dict[str, Any] = {"base_url": "", "pool": "", "api_key_file": "", "env": {}}
    raw = {k: d.get(k) for k in CONNECTION_KEYS}
    given = [k for k, v in raw.items() if v not in (None, "")]
    if not given:
        return empty, None
    url = raw["base_url"]
    if url in (None, ""):
        return empty, f"{', '.join(given)} needs a base_url"
    if harness != "claude-code":
        return empty, "base_url is for claude-code agents"
    if err := _url_error(url):
        return empty, err
    pool = raw["pool"]
    if pool in (None, ""):
        return empty, (
            "base_url needs a pool: the account aegis meters it on, "
            f"one of {', '.join(CONNECTION_POOLS)}"
        )
    if pool == "claude":
        return empty, "pool claude would read Claude's gauge for another endpoint"
    if pool == "codex":
        return empty, "pool codex has no gauge to read"
    if pool not in CONNECTION_POOLS:
        return empty, f"pool {pool!r} is not one of {', '.join(CONNECTION_POOLS)}"
    key_file = raw["api_key_file"]
    if key_file not in (None, "") and not isinstance(key_file, str):
        return empty, "api_key_file must be a path"
    if key_file and _VAR.search(key_file):
        return empty, "api_key_file takes a path, not a ${VAR}"
    env, env_error = _env(raw["env"])
    if env_error:
        return empty, env_error
    return {
        "base_url": url,
        "pool": pool,
        "api_key_file": os.path.expanduser(key_file) if key_file else "",
        "env": env,
    }, None


def _url_error(url: Any) -> str | None:
    if not isinstance(url, str):
        return "base_url must be an http or https URL"
    if _VAR.search(url):
        return "base_url takes no ${VAR}: write the URL itself"
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 -- raises on a port that is not a number
    except ValueError:
        return f"base_url {url!r} is not a URL"
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return f"base_url {url!r} is not an http or https URL"
    if "@" in parts.netloc:
        return (
            "base_url must not hold a user or password: .aegis.yaml is in git; "
            "put the key in api_key_file"
        )
    return None


def _env(raw: Any) -> tuple[dict[str, str], str | None]:
    """``env:`` as names to strings, or the error that stops the agent."""
    if raw in (None, ""):
        return {}, None
    if not isinstance(raw, dict) or not all(
        isinstance(k, str) and k and isinstance(v, str) for k, v in raw.items()
    ):
        return {}, "env must map variable names to strings"
    refused = [k for k in raw if k in ENV_REFUSED or k.endswith(ENV_REFUSED_SUFFIXES)]
    if refused:
        return {}, (
            f"env must not set {', '.join(refused)}: a credential or the config "
            "directory does not go in .aegis.yaml; the key goes in api_key_file"
        )
    return dict(raw), None


def _flag(switches: dict, key: str) -> bool:
    """A switch as the file has it, or its default when it is not a bool."""
    v = switches[key]
    return v if isinstance(v, bool) else SWITCHES[key]


def _models(d: dict) -> tuple[tuple[str, ...], tuple[str, ...], str | None]:
    """``models:`` without repeats, the repeats, and the error that stops the
    agent; ``((), (), None)`` when the agent gives ``model:`` alone."""
    raw = d.get("models")
    if raw is None:
        return (), (), None
    if d.get("model") not in (None, ""):
        return (), (), "give model or models, not both"
    if not isinstance(raw, list) or not all(isinstance(m, str) and m for m in raw):
        return (), (), "models must be a list of model names"
    if not raw:
        return (), (), "models is empty"
    names = [str(m) for m in raw]
    models = tuple(dict.fromkeys(names))
    repeated = tuple(dict.fromkeys(m for i, m in enumerate(names) if m in names[:i]))
    return models, repeated, None


def _keep(raw: Any) -> tuple[tuple[str, ...], str | None]:
    """``keep_harness_tools`` as names, or the error that stops the agent. A
    name aegis does not turn off is refused rather than ignored: it is most
    likely a typo, and a typo would silently keep nothing."""
    if raw is None:
        return (), None
    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        return (), "keep_harness_tools must be a list of tool names"
    unknown = [n for n in raw if n not in REPLACED_TOOLS]
    if unknown:
        return (), (
            f"keep_harness_tools names {', '.join(unknown)}, which aegis does not "
            f"turn off; it turns off {', '.join(REPLACED_TOOLS)}"
        )
    return tuple(raw), None


def read_config(config_root: Path) -> dict:
    path = config_root / CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        data = YAML(typ="safe").load(path.read_text())
    except YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e
    return data if isinstance(data, dict) else {}


def agents_from(data: dict) -> list[Agent]:
    """Every agent in a parsed config's ``agents:`` map."""
    agents = data.get("agents")
    if not isinstance(agents, dict):
        return []
    return [_agent(name, raw) for name, raw in agents.items()]


def load_agents(config_root: Path) -> list[Agent]:
    return agents_from(read_config(config_root))


def model_suggestions(agents: list[Agent]) -> dict[str, list[str]]:
    """Per harness, every model an agent of that harness names. The chip offers
    what the harness can run from its own catalog (``config.detect``); these
    are what it falls back to, and what it adds that the catalog lacks."""
    out: dict[str, list[str]] = {}
    for h in HARNESSES:
        models: list[str] = []
        for a in agents:
            if a.harness == h:
                models += [m for m in a.models if m not in models]
        out[h] = models
    return out


def resolve(
    agents: list[Agent],
    default: str | None,
    name: str | None,
    overrides: dict[str, str | None],
    cwd: Path,
    spawned_by: str | None = None,
    group: str | None = None,
    owner: str | None = None,
    within: Scope | None = None,
) -> SpawnSpec:
    """The spec a spawn runs: the agent's fields, each replaced by its override
    when one is given. The priming is always the agent's. Its scope is the
    agent's met with ``within``, the scope of the session that starts it."""
    name = name or default
    if not name:
        raise OpError("no_agent", "no agent given and no default_agent in .aegis.yaml")
    agent = next((a for a in agents if a.name == name), None)
    if agent is None:
        raise OpError("unknown_agent", f"no agent named {name!r}")
    if agent.error:
        raise OpError("bad_agent", f"agent {name!r}: {agent.error}")
    fields = {f: getattr(agent, f) for f in FIELDS}
    overridden = tuple(
        f
        for f in FIELDS
        if overrides.get(f) not in (None, "") and overrides[f] != fields[f]
    )
    fields |= {f: str(overrides[f]) for f in overridden}
    if fields["harness"] not in SUPPORTED_HARNESSES:
        raise OpError(
            "harness_unsupported", f"{fields['harness']} is not supported yet"
        )
    if err := _model_error(fields["harness"], fields["model"]):
        raise OpError("bad_model", err)
    # An explicit model is the person's choice: it runs alone, with no cascade.
    own = not {"model", "harness"} & set(overridden)
    cascade = agent.models if own and len(agent.models) > 1 else ()
    return SpawnSpec(
        agent=agent.name,
        model=fields["model"],
        effort=fields["effort"],
        permission=fields["permission"],
        cwd=cwd,
        harness=fields["harness"],
        priming=agent.priming,
        keep_harness_tools=agent.keep_harness_tools,
        **{k: getattr(agent, k) for k in SWITCHES},
        cascade=cascade,
        overridden=overridden,
        spawned_by=spawned_by,
        group=group,
        owner=owner,
        scope=agent.scope.meet(within),
    )
