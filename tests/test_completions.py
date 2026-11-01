"""``aegis completions`` writes a script the shell actually completes with.

The callback tests set ``_AEGIS_COMPLETE`` and invoke the real command, so a
completion cannot be a script that forgets to call aegis. Bash, fish and
nushell then source that script; zsh is not required to be installed.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aegis.cli import app
from aegis.completions import canonical_shell, install

SRC = Path(__file__).resolve().parents[1] / "src"
runner = CliRunner()


def invoke(*args: str, env: dict[str, str] | None = None):
    merged = {"COLUMNS": "200"}
    if env:
        merged.update(env)
    # The completion variable is _AEGIS_COMPLETE, from the program name. The
    # runner's default name is "root", and with no match the call falls
    # through to `aegis` with no command, which serves and never returns.
    return runner.invoke(app, list(args), env=merged, prog_name="aegis")


def home_env(tmp_path: Path) -> dict[str, str]:
    """A home that is not the person's, including the XDG paths install reads."""
    return {
        "HOME": str(tmp_path),
        "XDG_DATA_HOME": str(tmp_path / ".local" / "share"),
        "XDG_CONFIG_HOME": str(tmp_path / ".config"),
        "ZDOTDIR": str(tmp_path),
    }


def shim(tmp_path: Path) -> Path:
    """An ``aegis`` on PATH that runs this worktree, not whatever is installed.

    It has to be the program the shell executes. A ``python -c`` wrapper would
    report its own name, and the completion variable is taken from that name,
    so the call would miss ``_AEGIS_COMPLETE`` and serve instead.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    exe = bin_dir / "aegis"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        f"sys.path.insert(0, {str(SRC)!r})\n"
        "from aegis.cli import main\n"
        "main()\n"
    )
    exe.chmod(0o755)
    return bin_dir


def test_canonical_shell_accepts_nu_and_refuses_the_rest():
    assert canonical_shell("bash") == "bash"
    assert canonical_shell("Zsh") == "zsh"
    assert canonical_shell("nu") == "nushell"
    assert canonical_shell("nushell") == "nushell"
    assert canonical_shell("powershell") is None
    assert canonical_shell("") is None


def test_show_prints_a_script_for_each_shell():
    expected = {
        "bash": "_AEGIS_COMPLETE=complete_bash",
        "zsh": "_AEGIS_COMPLETE=complete_zsh",
        "fish": "_AEGIS_COMPLETE=complete_fish",
        "nushell": '_AEGIS_COMPLETE: "complete_nushell"',
    }
    for shell, marker in expected.items():
        result = invoke("completions", "show", shell)
        assert result.exit_code == 0, result.output
        assert marker in result.output
        assert "aegis" in result.output
    nu = invoke("completions", "show", "nu")
    assert nu.exit_code == 0, nu.output
    assert "complete_nushell" in nu.output


def test_show_refuses_a_shell_it_does_not_install():
    result = invoke("completions", "show", "powershell")
    assert result.exit_code == 2
    assert "powershell" in result.output


def test_install_writes_where_each_shell_loads_it(tmp_path):
    env = home_env(tmp_path)
    bash = invoke("completions", "install", "bash", env=env)
    assert bash.exit_code == 0, bash.output
    script = tmp_path / ".local/share/bash-completion/completions/aegis"
    assert "_AEGIS_COMPLETE=complete_bash" in script.read_text()
    rc = (tmp_path / ".bashrc").read_text()
    assert str(script) in rc
    assert rc.count("# >>> aegis completions bash >>>") == 1

    again = invoke("completions", "install", "bash", env=env)
    assert again.exit_code == 0, again.output
    assert "already sourced" in again.output
    assert (tmp_path / ".bashrc").read_text() == rc

    fish = invoke("completions", "install", "fish", env=env)
    assert fish.exit_code == 0, fish.output
    fish_script = tmp_path / ".config/fish/completions/aegis.fish"
    assert "complete_fish" in fish_script.read_text()
    assert "bashrc" not in fish.output

    zshrc = tmp_path / ".zshrc"
    zshrc.write_text("# keep\nautoload -Uz compinit && compinit\n")
    zsh = invoke("completions", "install", "zsh", env=env)
    assert zsh.exit_code == 0, zsh.output
    assert (tmp_path / ".zfunc" / "_aegis").is_file()
    text = zshrc.read_text()
    assert "# keep" in text
    block = text.split("# >>> aegis completions zsh >>>", 1)[1]
    assert "compinit" not in block
    assert "fpath" in text

    nu = invoke("completions", "install", "nushell", env=env)
    assert nu.exit_code == 0, nu.output
    assert (tmp_path / ".config/nushell/autoload/aegis.nu").is_file()


def test_install_keeps_lines_already_in_bashrc(tmp_path):
    env = home_env(tmp_path)
    (tmp_path / ".bashrc").write_text("# mine\nexport FOO=1\n")
    result = invoke("completions", "install", "bash", env=env)
    assert result.exit_code == 0, result.output
    text = (tmp_path / ".bashrc").read_text()
    assert text.startswith("# mine\nexport FOO=1\n")


def test_install_for_this_shell_uses_the_detected_name(tmp_path, monkeypatch):
    monkeypatch.setattr("aegis.completions.detect_shell", lambda env=None: "fish")
    result = invoke("completions", "install", env=home_env(tmp_path))
    assert result.exit_code == 0, result.output
    assert "installed fish completions" in result.output
    assert (tmp_path / ".config/fish/completions/aegis.fish").is_file()


def test_install_says_when_the_shell_cannot_be_told(monkeypatch):
    monkeypatch.setattr("aegis.completions.detect_shell", lambda env=None: None)
    result = invoke("completions", "install")
    assert result.exit_code == 2
    assert "which shell" in result.output


def _complete(env: dict[str, str]) -> str:
    result = invoke(env=env)
    assert result.exit_code == 0, result.output
    assert "http://" not in result.output
    return result.output


def test_the_callback_lists_subcommands_and_options():
    bash = _complete(
        {
            "_AEGIS_COMPLETE": "complete_bash",
            "COMP_WORDS": "aegis ser",
            "COMP_CWORD": "1",
        }
    )
    assert "serve" in bash.split()
    assert "service" in bash.split()
    assert "init" not in bash.split()

    flags = _complete(
        {
            "_AEGIS_COMPLETE": "complete_bash",
            "COMP_WORDS": "aegis --",
            "COMP_CWORD": "1",
        }
    )
    assert "--help" in flags.split()
    assert "--version" in flags.split()

    nested = _complete(
        {
            "_AEGIS_COMPLETE": "complete_bash",
            "COMP_WORDS": "aegis link ",
            "COMP_CWORD": "2",
        }
    )
    for name in ("add", "list", "trust", "remove"):
        assert name in nested.split()

    fish = _complete(
        {
            "_AEGIS_COMPLETE": "complete_fish",
            "_TYPER_COMPLETE_FISH_ACTION": "get-args",
            "_TYPER_COMPLETE_ARGS": "aegis ",
        }
    )
    assert "serve" in fish

    zsh = _complete(
        {"_AEGIS_COMPLETE": "complete_zsh", "_TYPER_COMPLETE_ARGS": "aegis ser"}
    )
    assert "serve" in zsh
    assert "service" in zsh

    nu = _complete(
        {
            "_AEGIS_COMPLETE": "complete_nushell",
            "_AEGIS_COMPLETE_WORDS": '["aegis", "ser"]',
        }
    )
    assert "serve\t" in nu or "\nserve" in nu or nu.startswith("serve")
    assert "service" in nu
    assert "init" not in nu.split()


def test_nushell_completes_paths_for_a_path_option(tmp_path, monkeypatch):
    folder = tmp_path / "workspace"
    folder.mkdir()
    (folder / "notes.txt").write_text("x")
    monkeypatch.chdir(folder)
    result = _complete(
        {
            "_AEGIS_COMPLETE": "complete_nushell",
            "_AEGIS_COMPLETE_WORDS": '["aegis", "serve", "--root", ""]',
        }
    )
    assert "notes.txt" in result


@pytest.mark.parametrize("shell", ["bash", "fish", "nu"])
def test_the_installed_script_completes_in_that_shell(tmp_path, shell):
    if shell == "nu" and not shutil_which("nu"):
        pytest.skip("nushell is not installed")
    if shell == "fish" and not shutil_which("fish"):
        pytest.skip("fish is not installed")
    bin_dir = shim(tmp_path)
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    shown = invoke("completions", "show", "nushell" if shell == "nu" else shell)
    assert shown.exit_code == 0, shown.output
    script = tmp_path / f"aegis.{shell}"
    script.write_text(
        shown.output if shown.output.endswith("\n") else shown.output + "\n"
    )
    if shell == "bash":
        out = subprocess.run(
            [
                "bash",
                "--noprofile",
                "--norc",
                "-c",
                """
source "$1"
COMP_WORDS=(aegis ser)
COMP_CWORD=1
COMP_LINE='aegis ser'
COMP_POINT=9
_aegis_completion aegis
printf '%s\n' "${COMPREPLY[@]}"
""",
                "bash",
                str(script),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=45,
        )
    elif shell == "fish":
        out = subprocess.run(
            [
                "fish",
                "--no-config",
                "-c",
                "source $argv[1]; complete -C 'aegis ser'",
                str(script),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=45,
        )
    else:
        # `source` wants a parse-time path, so the file is written into the
        # command rather than read from an environment variable.
        quoted = "'" + str(script).replace("'", "''") + "'"
        out = subprocess.run(
            [
                "nu",
                "-c",
                f"source {quoted}; 'aegis ser' | commandline complete | to text",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
            timeout=45,
        )
    assert out.returncode == 0, out.stderr
    assert "serve" in out.stdout
    assert "service" in out.stdout


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def test_library_install_can_be_pointed_at_a_home(tmp_path):
    """install() reads the env it is given, not the process home."""
    install("zsh", env=home_env(tmp_path))
    assert (tmp_path / ".zfunc" / "_aegis").is_file()
    text = (tmp_path / ".zshrc").read_text()
    assert "compinit" in text
