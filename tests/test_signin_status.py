import os
import stat
import sys
from pathlib import Path

from aegis.signin.recipes import load_recipes
from aegis.signin.status import harness_status

TOKEN = "sk-ant-oat01-" + "A1b2_C3d4-" * 9


def recipes(cmd: str):
    r = load_recipes()
    r["claude-code"].cmd = [cmd, "setup-token"]
    return r


def put(value: str) -> Path:
    p = Path(os.environ["AEGIS_CLAUDE_TOKEN_FILE"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(value + "\n")
    p.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return p


async def test_without_a_token_file_the_sign_in_is_unknown():
    (row,) = await harness_status(recipes(sys.executable))
    assert row["harness"] == "claude-code" and row["installed"]
    assert row["signed_in"] is None and row["expires"] is None


async def test_a_kept_token_says_signed_in_until_about_a_year_on():
    p = put(TOKEN)
    (row,) = await harness_status(recipes(sys.executable))
    assert row["signed_in"] is True
    assert abs(row["expires"] - (p.stat().st_mtime + 365 * 86400)) < 1


async def test_a_harness_not_on_path_is_not_installed():
    (row,) = await harness_status(recipes("no-such-harness-xyz"))
    assert row["installed"] is False and row["version"] is None
