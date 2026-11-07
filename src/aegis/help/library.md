---
title: aegis as a library
kind: howto
when: building your own program on aegis, starting an aegis server from Python, or driving sessions from code instead of a browser
hints:
  - Run aegis inside your own Python program and drive its sessions from code. #start-a-server
  - session.ask() sends a message and returns the turn it started, typed. #drive-a-session
  - Enqueue tasks, run workflows and set schedules from Python. #queues-workflows-and-schedules
---

A Python program can run an aegis server inside its own event loop and drive
it through typed objects: a `Server`, its sessions as `Session` handles, and
every other operation as a typed method on `srv.ops`. The names exported by
`aegis` itself (`Server`, `Session`, `Turn`, `Task`, `WorkflowRun`,
`Schedule`, `StartError`, `OpError`, `SessionNotFound`) and by `aegis.api`
are the stable surface. Every other
module is internal and may change in any release.

## Start a server

```python
import asyncio
from aegis import Server

async def main():
    async with Server("./myproj", name="lab") as srv:
        print(srv.url)   # the web client, signed in
        ...

asyncio.run(main())
```

`Server` takes the options of `aegis serve`: the root, the folder holding
`.aegis.yaml` (required, since a library never guesses from the working
directory), `port` (0, the default, takes a free one),
`host`, `name`, and the harness binaries (`claude`, `opencode`, `codex`,
`cursor`). Entering the block runs the startup checks, boots aegis and
listens. Leaving it shuts aegis down as a stopped `aegis serve` does.

- It always listens, on loopback unless you pass `host`. The agents it
  starts reach aegis's tools at the server's MCP endpoint, under
  `srv.base_url`, so a server without a listener would start agents with no
  aegis tools. The web
  client at `srv.url` is there if you want to watch.
- A failed startup check raises `StartError`. Its `lines` hold what
  `aegis serve` would print.
- It installs no signal handlers. Your program decides when aegis stops, by
  leaving the block.
- One Server runs per process at a time. A second one raises `RuntimeError`
  until the first has exited.
- Every call runs as the server's admin, as a browser with the owner's token
  does.

## Drive a session

```python
from aegis.api import Prose, ToolCall, TurnEnded

s = await srv.sessions.spawn(agent="opus", cwd="repo")
turn = await s.ask("summarize the README", timeout=600)
print(turn.text, turn.attention, turn.cost_usd)

await s.send("now write tests for it")
async for ev in s.events():
    match ev:
        case Prose(md=md):
            print(md)
        case ToolCall(title=title, status=status):
            print("tool", title, status)
        case TurnEnded():
            break
await s.close()
```

`srv.sessions` has `spawn(prompt=None, *, agent, harness, model, effort,
permission, cwd, quiet)`, `list()` and `get(ref)`, where `ref` is a log id or
a handle. A `Session` is a live handle known by its log id. Its methods act
on the real session, so a Session kept in a variable never goes stale:

- `info()` returns a `SessionInfo` snapshot of the session's card: handle,
  title, agent, model, state (working, idle, asleep, error), attention, plan,
  cost.
- `send(text)` delivers a message and returns once it is accepted.
- `ask(text)` sends and waits for the turn to end, and `wait()` waits for the
  running one. Both return a `Turn`: its attention and line, the turn's text,
  its entries and, for `ask`, what it cost.
- `events(history=False)` yields each transcript entry once it settles
  (`UserMessage`, `Prose`, `Thinking`, `ToolCall`, `SystemNote`, `ErrorNote`,
  or `OtherEntry` for a kind the library does not model yet), and
  `TurnEnded` when a turn ends. Leaving the loop unsubscribes.
- `entries()`, `interrupt()`, `stop()`, `close()` and `rename(handle, title)`
  do what their names say. A closed session raises `SessionNotFound`.

## Queues, workflows and schedules

```python
task = await srv.queues.enqueue("review", "Review the diff in PR 12 ...")
info = await task.wait(timeout=1800)          # TaskInfo
print(info.status, info.result)               # completed, the worker's final message

run = await srv.workflows.arm(definition, owner=s, approve=True)
info = await run.wait(timeout=3600)           # RunInfo: ended, or waiting on an answer
for item in info.open:
    await run.answer(item.to, choice="continue")

sch = await srv.schedules.create("nightly-check", cron="0 6 * * 1-5", prompt="...")
await sch.pause()
```

These are live handles too, by task id, run id and schedule name:

- `srv.queues.enqueue(queue, payload)` hands a self-contained prompt to a
  fresh worker on a queue from `.aegis.yaml` and returns a `Task`, with
  `info()`, `wait()`, `cancel()`, `resume()` and `worker()`. A task a program
  enqueues has no enqueuer session, so its result reaches no inbox: read it
  from `TaskInfo.result`.
- `srv.workflows.arm(definition, owner=session)` arms a workflow, written as
  for `workflow_arm`, with `owner` as its main agent. aegis wakes that
  session when a gate, a worker's question or a failed step needs an answer,
  so a run always has one. It runs once approved: pass `approve=True`, or call
  `approve()` later. A `WorkflowRun` has `info()` (its status and a typed row
  per node), `wait()`, which returns when the run ends or when `open` lists
  something to answer, `answer(to, choice=..., value=..., text=...)`, sent as
  the owner, and `stop()`, `reject()` and `owner()`.
- `srv.schedules.create(name, prompt=..., every=|cron=|at=...)` fires a
  prompt on a clock, into a new session from `agent` or into an existing
  `session`'s inbox, after an optional bash `check`. A `Schedule` has
  `info()`, `update(...)`, `pause()`, `resume()`, `fire()` and `delete()`. A
  schedule a program creates is the admin's, so it starts unpaused.

## Call any operation

Everything aegis can do is an operation, and `srv.ops` has each one as a
method with keyword arguments: `srv.ops.queue.enqueue(queue="review",
prompt="...")`, `srv.ops.browser.goto(browser="b1", url="...")`. In a name
with more than one dot, the dots after the first become underscores, so
`workflow.edit.apply` is `srv.ops.workflow.edit_apply()`. An operation with
no dot is a method of `srv.ops` itself, such as `srv.ops.meta()`. The
arguments are checked by the operation's own model, and a wrong one raises
`OpError` with the code bad_params. The results are what the browser and the
agents get: dicts, lists and strings.

The signatures are in `src/aegis/api/ops.pyi`, so an editor completes them
and a type checker checks them. After changing an operation's parameters,
run `make ops-stubs` and commit the file. A test fails while it is stale.

## Host policy

A program that embeds aegis decides who may do what through a host policy:
`Server(root, policy=MyPolicy())`, where `MyPolicy` subclasses
`aegis.HostPolicy` and overrides the hooks it needs. Each hook's default
allows. `aegis serve` runs `StandalonePolicy`: one caller, the holder of the
server's token, who may do everything, and linked servers as before.

aegis calls each hook with a `who`: the object your `identify` returned, kept
as it is and never read. For an agent's call, `who` is the person whose
session it is. `who` is `None` for the server's own caller: its token, `srv`
without `srv.as_who(...)`, and aegis's own work.

| Hook | When aegis asks |
|---|---|
| `identify(cred)` | A browser's hello, an HTTP request or a linked server's hello brings a token aegis does not read itself. Return a who, or raise `Refused`. The server's own token and a token from `srv.token_for(...)` never reach it, except over a link. The default refuses links. |
| `key(who)`, `restore(key)` | aegis stores `key(who)` wherever it keeps an owner (sessions, schedules, queue tasks, workflow runs), and calls `restore` to get the who back with nobody connected, such as after a restart. A `restore` that raises `Refused` stops that work with your message. |
| `guard(op, who, caller)` | Before every operation. The default keeps aegis's own checks (operations closed to agents, read-permission sessions, schedule runs with `spawn: false`, operations closed to links); call `super().guard(...)` to keep them. |
| `can(who, action, session)` | `action` is `"list"`, `"read"` or `"write"`. Before a session appears in the tab bar, `session_list`, the archive or the journal; before its transcript is read or subscribed to; before a sent file of its is served; and before anything is sent to it or changed on it. |
| `catalog(who)` | What the pickers offer: agents, harnesses, models, efforts, permissions, folders. A start outside it is refused. |
| `spawn(who, req)` | Before any session starts: a spawn, a handoff, a queue worker, a schedule or workflow run, and a stopped session waking. Return the `SpawnRequest` to run, rewritten with `req.replace(...)`, or raise `Refused`. On a wake only model, effort and permission change. `env` reaches the process and is never written to disk. |
| `tools(who, session)` | When a harness starts: the names of the aegis tools it gets, or `None` for all. The rest are hidden from its tool list and refused when called. |
| `mcp(who, session)` | Extra MCP servers for the session. Defined now; aegis calls it from the release that gives each harness its MCP servers. |
| `around(op, who, params, call)` | Around every operation, after the checks above: refuse, change `params`, call `await call(params)`, change the result. |
| `on_event(who, event)` | After a session starts, writes an entry, ends a turn, stops, closes or sends a file. Events arrive in order, and a slow or failing hook never holds up a turn. |

A session the caller may not read answers as missing, with the code a
session, task or run that does not exist gets, so nobody learns it exists; one they may read but not
change answers `refused`. A session carries `tags`, strings your `spawn` sets
with `req.replace(tags=...)`; every session it starts inherits them, so `can`
can decide from the session alone.

`srv.token_for(who, ttl=3600)` mints a signed token for your page to hand the
client: it holds the caller's key and an expiry, needs no table, and survives
a restart until it expires. `srv.as_who(who)` is the same API acting as that
person: `await srv.as_who(who).sessions.spawn()` starts a session of theirs.

Some operations act on the whole server rather than on a session: the
config, links, the Files view, terminals, harness sign-ins, usage, Pause all
and updates. `HostPolicy.guard` refuses them to every caller you identified,
and only the server's own caller keeps them; override `guard` to open one.
A schedule belongs to the person who made it, and others neither see nor
change it.

Your `key(who)` must differ from the server's own key (the user `aegis serve`
runs as), which stands for the server itself; aegis refuses a caller whose key
is that. aegis asks `restore` once per key and process, and keeps the who
until a restart.

Memories, skills and help state are kept per key; dictation, saved workflow
definitions and harness sign-ins are the server's.
