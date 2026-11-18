---
title: Links
kind: concept
when: working with sessions on another aegis server, from this one
hints:
  - Link another aegis server and start sessions on it from this one. #add-a-link
---

A link joins two aegis servers both ways. The people on each see and drive the
other's sessions in the same browser, and the agents on each act on the other as
at home: they start sessions there, read them, hand them work, enqueue, wait and
use its schedules. Linking servers federates them, so link only servers you
use. aegis has no protection between linked servers.

## Add a link

From a shell on this machine, pipe the far server's token into
`aegis link add <name> <url>`. The far server's token is in its state
directory, in `.aegis/state/token` under its root:

```bash
ssh far-host cat work/.aegis/state/token | aegis link add far http://far-host:8742
```

One server opens the link and the other reaches back over the same socket, so
the server behind NAT or a VPN, such as a laptop, is the one that adds it.

aegis connects once and checks that the far server calls itself by that
name, which is its `aegis serve --name` (the host name by default). Pass
`--as` with this server's own `--name` when it runs with one, since the check
introduces this server by the host name otherwise. A running server picks the
link up within a second, with no restart.

An admin can also link from the Settings page, under Servers, with the far
server's URL and token. `aegis link list` shows the links by name and URL,
never their tokens, and `aegis link remove <name>` ends one.

Links are kept in this server's state directory with owner-only permissions,
because each holds a token. They are never in `.aegis.yaml`, which may be in
git. Both servers need aegis 3.0.0 or newer.

One link joins a pair of servers. If the far server already links this one, it
refuses the second link and says so; remove one of the two.

## What people can do across a link

A linked server's sessions appear in the tab bar and the Fleet with the
server's name beside the handle, and its archive merges into this one's.
The new-tab page gains a server picker. Through the link a person here can
read, prompt, interrupt, stop and close those sessions, and spawn new ones
(`/spawn agent@server`). The same holds from the far server's browser: it shows
this server's sessions, with this server's name beside the handle, while the
link is up, and its Settings lists this server under Servers as one that
links here. From the far side, this server's archive and the files sent here
are not shown.

## What agents can do across a link

A session on a linked server is addressed `handle@server`, and agents on both
servers act on it as at home:

- `session_spawn` with `agent@server`, at most at the agent's own permission,
  when that server's quota gate is open;
- `peer_handoff` to `handle@server`; it arrives headed
  `> from agent:<handle>@<server> (<user>)`;
- `session_list` lists the other server's sessions in full (title, attention,
  plan, cwd) with `server`, and `peer_read` reads `handle@server`;
- `queue_enqueue` with `queue@server`, whose result comes back to the inbox,
  and `task_status`, `task_cancel` and `task_resume` with `task-id@server`;
- `monitor_sessions` on `handle@server`, and `session_close` on
  `handle@server`, which closes a session the agent started there at once when
  it is done and any other on a second call with the token, as at home;
- every `schedule_*` tool with `name@server`: the schedule lives and ticks on
  that server, and it [waits for a person](schedules#an-agent-s-schedule-waits-for-you) as at home.

An error the other server returns reaches the agent in its own words, after the
server's name. Nothing is relayed: a call that came over a link never goes on to
a third server, so servers that must reach each other link directly.

## Exit through a link

A server can send what it starts out to the internet through a link,
from the far server's address, with no VPN and no change to the machine's
routes. Name the link in `.aegis.yaml`:

```yaml
exit_via: host
```

or press Exit here on its row in Settings, Servers, which writes it in this
server's block under `hosts:`, so the other servers reading the same file in git
keep their own route ([config](config#per-host-overrides)). Sessions, queue workers,
workflow steps, monitors, skill tools, terminals and browsers started after that
go through a listener on loopback: each gets the proxy variables, Chromium its
launch setting, and `git` over SSH a `GIT_SSH_COMMAND`, unless you set one. A running process keeps the
environment it started with, so a change applies to the next one started, and a
session's sidebar says which exit it has. The Host rows gain a Via row with the
address they exit from.

One agent can differ from the rest, with `exit:` naming a link or `off`:

```yaml
agents:
  opus: {harness: claude-code, model: opus}
  opus-local: {harness: claude-code, model: opus, exit: "off"}
```

A terminal profile whose `env:` names `HTTPS_PROXY` or `HTTP_PROXY` keeps it.
The server's own work stays on its own route: `git` and `systemctl` it runs,
its doctor, its version checks and its network probes.

What it does not cover:

- A program that ignores the proxy variables goes out directly: a bare `ssh`, a
  raw socket, UDP. `curl`, `git` over HTTPS, `gh`, `npm`, `uv`, Python, Node's
  `fetch` and Claude Code all honour them. It is no guarantee for every packet.
- The relay connects to public addresses only, so an agent cannot reach the far
  server's private network through it.

When the link is down, a connection through the exit fails with a 502 that says
why, and nothing falls back to this machine's address. `aegis doctor` flags a
name that is not a link.

The near server must reach the far server's aegis URL without the VPN: a far
server only on an overlay network cannot replace that overlay. The far server
sees the host and port of each HTTPS connection and all of any plain HTTP, and
Settings on it shows the bytes each link took.

An exit changes the address and the country a provider sees, and does not change
what the provider's terms allow. It is not a way around a provider's rules about
where its service may be used.

## When a link is down

A link is linked, connecting, offline, unauthorized (the far server refused
the token; it does not retry, and a new token needs a new link), mismatch
(a different name or an incompatible protocol), too_old (one of the servers runs
an aegis older than 3.0.0) or refused (the far server already links this one). An offline link reconnects on
its own, backing off up to 30 seconds, a mismatched, too_old or refused one
tries again every 30 seconds, and a dropped link fails every call in flight. The Fleet and the
Settings page show a server that is down as offline since the time it went
down.
