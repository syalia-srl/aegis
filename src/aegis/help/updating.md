---
title: Updating aegis
kind: howto
when: a newer aegis release is out, upgrading aegis from the browser or a shell, the update arrow next to the version, an update that failed
hints:
  - When a new aegis is out, press Update beside the version to install it and restart. #update-from-the-browser
---

aegis checks PyPI for a newer release once an hour. When one is out, an
arrow shows next to the version, in the top bar and at the foot of a
session's panel. This page says how to install it from there, which installs
aegis can upgrade itself, and what to do when an update fails.

## Update from the browser

An admin sees an **Update** button beside the arrow: in the top bar on a
desktop, and in the version card at the foot of the session panel on any
screen (hover or tap the version to open the card). The card also says which
installer owns this install and the command it will run.

Press it and confirm. The confirmation says how many sessions are working:
their turns stop for the restart and continue on the new version, as after
any restart of the server. The card then shows three steps:

1. **installing**: the installer runs, and its last lines show in the card.
2. **checking**: aegis runs the new build's `aegis --version`. Only a new
   version number goes on; anything else stops here, with the old server
   still running.
3. **restarting**: aegis stops cleanly and starts the new build in the same
   process, with the same arguments. The process id stays the same, so a
   systemd unit sees no restart and needs no `Restart=` setting.

The page reconnects, finds a newer build and reloads, and the version shows
as current. A member sees the arrow and the command, but no button.

## Which installs aegis can update

aegis reads how it was installed from its own running process and upgrades
with the same tool, so that one copy stays on the machine:

| install | command |
|---|---|
| `uv tool install aegis-harness` | `uv tool upgrade aegis-harness` |
| `pipx install aegis-harness` | `pipx upgrade aegis-harness` |
| a virtual environment set up by uv | `uv pip install --python <its python> -U aegis-harness` |
| a virtual environment set up by pip | `<its python> -m pip install -U aegis-harness` |

A build from git or an editable install, a `uvx` run, and a system or conda
Python get no button. The card shows the command to run in a shell instead,
and after it you restart the server yourself (`systemctl restart`, or stop
and start `aegis serve`).

## When an update fails

A failed step turns red, and the card keeps the installer's output and says
that the old version is still running. Nothing was restarted. Common causes:

- **No network, or PyPI unreachable.** The installer's error says so.
  **Try again** once the server is online.
- **A pinned install.** `uv tool install aegis-harness==2.20.0` stays on
  2.20.0 through `uv tool upgrade`, so the check finds the same version and
  stops. Install again without the pin, in a shell.
- **A new build that does not start.** The check reports that it did not
  answer `--version`. The files on disk are already the new ones, so restart
  only after fixing the install in a shell.
