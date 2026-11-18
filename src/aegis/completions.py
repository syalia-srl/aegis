"""``aegis completions``: install tab completion for bash, fish, zsh and nushell.

Bash, fish and zsh use Typer's completion scripts. Each script calls ``aegis``
again with ``_AEGIS_COMPLETE`` set, and Typer answers from the command tree,
so a new subcommand completes without reinstalling. Typer has no nushell
script; that shell gets one here, speaking the same callback.

``Typer(add_completion=False)`` keeps ``--install-completion`` off the help
(it cannot name nushell). The callback still runs, but only after the shell
classes are registered, which ``register`` does at startup.
"""

from __future__ import annotations

import json
import os
import re
import shlex
from collections.abc import Mapping
from pathlib import Path

import typer
from typer._click.shell_completion import CompletionItem, ShellComplete

SHELLS = ("bash", "fish", "zsh", "nushell")
PROG = "aegis"
COMPLETE_VAR = "_AEGIS_COMPLETE"

_BEGIN = "# >>> aegis completions {shell} >>>"
_END = "# <<< aegis completions {shell} <<<"


def register() -> None:
    """Teach Typer's completion callback the four shells, including nushell."""
    from typer._click.shell_completion import add_completion_class
    from typer._completion_classes import completion_init

    completion_init()
    add_completion_class(NushellComplete, "nushell")


def canonical_shell(name: str) -> str | None:
    """``bash`` as itself, ``nu`` as nushell, anything else as nothing."""
    key = name.strip().lower()
    if key == "nu":
        key = "nushell"
    if key in SHELLS:
        return key
    return None


def detect_shell(env: Mapping[str, str] | None = None) -> str | None:
    """The shell this process was started from, if it is one we install for."""
    found = ""
    try:
        import shellingham

        name, _cmd = shellingham.detect_shell()
        found = name or ""
    except Exception:
        source = os.environ if env is None else env
        found = Path(source.get("SHELL", "")).name
    return canonical_shell(found)


def resolve_shell(name: str | None, env: Mapping[str, str] | None = None) -> str:
    """A shell the person named, or the one they are in. Exits 2 otherwise."""
    if name is None or not name.strip():
        found = detect_shell(env)
        if found is None:
            typer.echo(
                "could not tell which shell this is. "
                "name one of bash, fish, zsh, nushell.",
                err=True,
            )
            raise typer.Exit(2)
        return found
    found = canonical_shell(name)
    if found is None:
        typer.echo(f"{name} is not one of bash, fish, zsh, nushell.", err=True)
        raise typer.Exit(2)
    return found


class Layout:
    """Where a shell reads completion and startup files. Tests pass a fake home."""

    def __init__(self, home: Path, data: Path, config: Path, zdot: Path) -> None:
        self.home = home
        self.data = data
        self.config = config
        self.zdot = zdot

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Layout:
        source = os.environ if env is None else env
        home = Path(source["HOME"]) if source.get("HOME") else Path.home()
        data = (
            Path(source["XDG_DATA_HOME"])
            if source.get("XDG_DATA_HOME")
            else home / ".local" / "share"
        )
        config = (
            Path(source["XDG_CONFIG_HOME"])
            if source.get("XDG_CONFIG_HOME")
            else home / ".config"
        )
        zdot = Path(source["ZDOTDIR"]) if source.get("ZDOTDIR") else home
        return cls(home, data, config, zdot)

    def script(self, shell: str) -> Path:
        if shell == "bash":
            return self.data / "bash-completion" / "completions" / PROG
        if shell == "fish":
            return self.config / "fish" / "completions" / f"{PROG}.fish"
        if shell == "zsh":
            return self.zdot / ".zfunc" / f"_{PROG}"
        return self.config / "nushell" / "autoload" / f"{PROG}.nu"

    def startup(self, shell: str) -> Path | None:
        """The file that must source the script. Fish and nushell load theirs."""
        if shell == "bash":
            return self.home / ".bashrc"
        if shell == "zsh":
            return self.zdot / ".zshrc"
        return None


def script_for(shell: str) -> str:
    """The completion script for one of the four shells."""
    if shell == "nushell":
        return _NUSHELL
    from typer._completion_shared import get_completion_script

    return get_completion_script(prog_name=PROG, complete_var=COMPLETE_VAR, shell=shell)


def show(shell: str | None, env: Mapping[str, str] | None = None) -> str:
    return script_for(resolve_shell(shell, env))


def install(shell: str | None, env: Mapping[str, str] | None = None) -> None:
    """Write one shell's script where that shell loads it, and say where."""
    name = resolve_shell(shell, env)
    layout = Layout.from_env(env)
    path = layout.script(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script_for(name).rstrip() + "\n")
    startup = layout.startup(name)
    changed = False
    if startup is not None:
        changed = _ensure_startup(startup, name, path, layout)
    typer.echo(f"installed {name} completions in {path}")
    if startup is not None:
        if changed:
            typer.echo(f"added them to {startup}")
        else:
            typer.echo(f"already sourced from {startup}")
    typer.echo("open a new shell to use them")


def _ensure_startup(path: Path, shell: str, script: Path, layout: Layout) -> bool:
    begin, end = _BEGIN.format(shell=shell), _END.format(shell=shell)
    body = _startup_body(shell, script, layout, path, begin, end)
    block = f"{begin}\n{body}\n{end}\n"
    text = path.read_text() if path.is_file() else ""
    pattern = re.compile(re.escape(begin) + r".*?" + re.escape(end) + r"\n?", re.S)
    if pattern.search(text):
        new = pattern.sub(lambda _m: block, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        new = text + block
    if new == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    return True


def _startup_body(
    shell: str,
    script: Path,
    layout: Layout,
    startup: Path,
    begin: str,
    end: str,
) -> str:
    if shell == "bash":
        quoted = shlex.quote(str(script))
        return f"[ -f {quoted} ] && . {quoted}"
    zfunc = shlex.quote(str(layout.zdot / ".zfunc"))
    lines = [f"fpath=({zfunc} $fpath)"]
    existing = startup.read_text() if startup.is_file() else ""
    outside = re.sub(
        re.escape(begin) + r".*?" + re.escape(end) + r"\n?", "", existing, flags=re.S
    )
    if "compinit" not in outside:
        lines.append("autoload -Uz compinit && compinit")
    return "\n".join(lines)


_NUSHELL = r"""# Tab completion for aegis. `aegis completions install nushell` writes this file.
# Nushell loads every file in its autoload directory at startup.

def "nu-complete aegis" [token: record, place: record] {
    let words = $place.command
    let text = $token.text
    let argv = if ($words | is-empty) {
        ["aegis" $text]
    } else if ($text | is-empty) {
        if ($words | last) == "" { $words } else { $words | append "" }
    } else if ($words | last) == $text {
        $words
    } else {
        $words | append $text
    }
    let raw = (with-env {
        _AEGIS_COMPLETE: "complete_nushell"
        _AEGIS_COMPLETE_WORDS: ($argv | to json)
    } { ^aegis | complete })
    if $raw.exit_code != 0 {
        return []
    }
    $raw.stdout | lines | where {|line| not ($line | is-empty)} | each {|line|
        let parts = ($line | split row "\t")
        if ($parts | length) > 1 {
            {value: ($parts | first), description: ($parts | skip 1 | str join "\t")}
        } else {
            $parts | first
        }
    }
}

extern "aegis" [
    ...rest: string@"nu-complete aegis"
]
"""


class NushellComplete(ShellComplete):
    """Answers ``_AEGIS_COMPLETE=complete_nushell`` the way the nushell script asks.

    A path parameter comes back from Click as a single file or directory
    marker. The other shells turn that into their own filename completion;
    nushell has no such hook from this script, so the marker is expanded
    to the matching paths.
    """

    name = "nushell"
    source_template = ""

    def source_vars(self) -> dict[str, str]:
        return {
            "complete_func": self.func_name,
            "complete_var": self.complete_var,
            "prog_name": self.prog_name,
        }

    def format_completion(self, item: CompletionItem) -> str:
        if item.type in {"file", "dir"}:
            return ""
        value = str(item.value).replace("\t", " ")
        help_ = " ".join((item.help or "").split())
        return f"{value}\t{help_}" if help_ else value

    def get_completion_args(self) -> tuple[list[str], str]:
        raw = os.environ.get("_AEGIS_COMPLETE_WORDS", "")
        try:
            words = json.loads(raw) if raw else []
        except json.JSONDecodeError:
            words = []
        if not isinstance(words, list) or not words:
            return [], ""
        text = [str(word) for word in words]
        return text[1:-1], text[-1]

    def complete(self) -> str:
        from typer._click.shell_completion import _resolve_context, _resolve_incomplete

        args, incomplete = self.get_completion_args()
        ctx = _resolve_context(self.cli, self.ctx_args, self.prog_name, args)
        obj, incomplete = _resolve_incomplete(ctx, args, incomplete)
        items = obj.shell_complete(ctx, incomplete)
        # Typer's path type completes nothing, where Click's file type returns
        # one marker the other shells turn into filenames. Nushell needs the
        # names itself.
        kind = getattr(getattr(obj, "type", None), "name", "")
        if kind in {"path", "file", "dir"} and (
            not items or all(item.type in {"file", "dir"} for item in items)
        ):
            dirs_only = kind == "dir" or (
                bool(items) and all(item.type == "dir" for item in items)
            )
            return "\n".join(_path_matches(incomplete, dirs_only=dirs_only))
        return "\n".join(
            line for line in (self.format_completion(item) for item in items) if line
        )


def _path_matches(incomplete: str, *, dirs_only: bool) -> list[str]:
    """One directory level of names starting with the incomplete path."""
    if not incomplete or incomplete.endswith("/"):
        folder = Path(incomplete or ".")
        prefix = ""
        base = incomplete
    else:
        path = Path(incomplete)
        folder = path.parent
        prefix = path.name
        parent = path.parent.as_posix()
        base = "" if parent == "." else parent + "/"
    try:
        children = sorted(folder.iterdir(), key=lambda child: child.name)
    except OSError:
        return []
    out = []
    for child in children:
        name = child.name
        if prefix:
            if not name.startswith(prefix):
                continue
        elif name.startswith("."):
            continue
        if dirs_only and not child.is_dir():
            continue
        shown = f"{base}{name}"
        if child.is_dir():
            shown += "/"
        out.append(shown)
    return out
