"""Per-harness login recipes: the command to run and the screens to recognise."""

import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from typing import Literal

from aegis.signin.mask import DEFAULT_SHAPES

Step = Literal[
    "starting",
    "open_url",
    "paste",
    "device_code",
    "choose",
    "checking",
    "done",
    "failed",
]
_STEPS = set(Step.__args__)
_RECIPE_KEYS = {"cmd", "env", "unset", "cols", "timeout_s", "shapes", "rules"}
_RULE_KEYS = {"name", "step", "all", "capture"}


@dataclass
class Rule:
    name: str
    step: Step
    all: list[re.Pattern]
    capture: dict[str, re.Pattern] = field(default_factory=dict)


@dataclass
class Recipe:
    harness: str
    cmd: list[str]
    env: dict[str, str]
    unset: list[str]
    cols: int
    timeout_s: int
    shapes: list[re.Pattern]
    rules: list[Rule]

    def match(self, screen_text: str) -> tuple[Rule, dict[str, str]] | None:
        for rule in self.rules:
            if all(p.search(screen_text) for p in rule.all):
                found = {}
                for key, p in rule.capture.items():
                    m = p.search(screen_text)
                    if m:
                        found[key] = m.group(1)
                return rule, found
        return None


def _unknown(where: str, got: dict, known: set[str]) -> None:
    extra = sorted(set(got) - known)
    if extra:
        raise ValueError(f"sign-in.toml: unknown key {extra[0]!r} in {where}")


def parse_recipes(text: str) -> dict[str, Recipe]:
    out = {}
    for harness, t in tomllib.loads(text).items():
        _unknown(f"[{harness}]", t, _RECIPE_KEYS)
        rules = []
        for r in t.get("rules", []):
            _unknown(f"[{harness}] rule {r.get('name')!r}", r, _RULE_KEYS)
            if r["step"] not in _STEPS:
                raise ValueError(
                    f"sign-in.toml: unknown step {r['step']!r} in [{harness}]"
                )
            rules.append(
                Rule(
                    r["name"],
                    r["step"],
                    [re.compile(p) for p in r["all"]],
                    {k: re.compile(p) for k, p in r.get("capture", {}).items()},
                )
            )
        out[harness] = Recipe(
            harness,
            t["cmd"],
            t.get("env", {}),
            t.get("unset", []),
            t.get("cols", 600),
            t.get("timeout_s", 600),
            DEFAULT_SHAPES + [re.compile(p) for p in t.get("shapes", [])],
            rules,
        )
    return out


def load_recipes() -> dict[str, Recipe]:
    return parse_recipes(
        resources.files("aegis").joinpath("data/sign-in.toml").read_text()
    )
