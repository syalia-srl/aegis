"""What Settings shows for each harness that has a sign-in recipe (#946):
whether it is installed, its version, and what aegis knows of its sign-in."""

from __future__ import annotations

from ..claude import login
from ..harness_versions import read_version
from .recipes import Recipe


async def harness_status(recipes: dict[str, Recipe]) -> list[dict]:
    """One row per recipe. ``signed_in`` is True with ``expires`` when aegis
    keeps a usable token for the harness, and None when it cannot tell (the
    harness's own stored sign-in); ``problem`` says why a kept token is unusable."""
    rows = []
    for harness, recipe in recipes.items():
        version, _path = await read_version(recipe.cmd[0])
        row: dict[str, object] = {
            "harness": harness,
            "installed": version is not None,
            "version": version,
            "signed_in": None,
            "expires": None,
            "problem": None,
        }
        if harness == "claude-code":
            row["problem"] = login.file_problem(login.token_path())
            if (when := login.expiry()) is not None:
                row["signed_in"], row["expires"] = True, when[1]
        rows.append(row)
    return rows
