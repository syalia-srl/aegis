---
title: Feeds
kind: howto
when: acting on each new part of a source that keeps growing, such as a chat log appended to a file or a command's new output, without re-arming a monitor
hints:
  - A feed wakes an agent with each batch of new lines from a file that grows. #start-a-feed
  - A feed can read a command that hands back a cursor. #a-command
---

A feed binds a source that keeps growing to your session. aegis reads it on a
timer, keeps the cursor, and wakes you with each batch of new items as one
message. It stays armed until you cancel it or your session closes, so you
never re-arm anything, and an item written while you work is not lost.

Use a monitor to wait for one thing to finish. Use a feed when you must act on
every new part of something that never finishes.

## Start a feed

Call `feed_start` with:

- `name`: lowercase words joined by hyphens, unique among your feeds (at most
  five);
- `source`: a file or a command, described below;
- `debounce_s=15`: how long the source must stay quiet before a batch goes. A
  batch never waits more than two minutes after its oldest item;
- `max_items=50`: items per message at most; a message also stops at 32 KB,
  and the rest comes in the next one;
- `cwd` (optional): where a relative path or the command resolves, relative to
  your session's working directory.

The reply has the feed, the result of its first read, and your other feeds.
Then end your turn.

## A file

```json
{
  "name": "group-chat",
  "source": {
    "file": "~/.cache/pickup/groups/-100123.jsonl",
    "format": "jsonl",
    "filter": "select(.text) | {from, text}",
    "from": {"line": 204}
  }
}
```

- `format`: `lines` (the default) makes each non-blank line an item; `jsonl`
  reads one JSON value per line.
- `from`: `end` (the default) reads only what is written after the start,
  `start` reads the whole file, and `{"line": N}` starts after line N, so a
  session that kept its own count moves to a feed without reading anything
  twice.
- `filter` (jsonl only): a `jq` expression run on each line. What it prints is
  the item; a line it prints nothing for is dropped and never wakes you. A line
  that is not JSON skips the filter and arrives as it is. A filter that does
  not compile is refused at the start.
- `interval_s=5`: how often the file is read, in seconds.

Only lines that end in a newline count, so a half-written line waits for the
next read. A file that shrinks or is replaced is read again from its start,
and the next message says so.

## A command

```json
{"name": "new-issues", "source": {"command": "bin/new-issues", "interval_s": 60}}
```

The command runs under bash every `interval_s=30` seconds by default:

- it gets its cursor in `$AEGIS_FEED_CURSOR`, empty the first time;
- it prints one item per line on stdout;
- it writes the next cursor to the file `$AEGIS_FEED_NEXT`;
- it exits 0 when it printed items, 1 when there is nothing new, and anything
  else on an error.

```bash
since=${AEGIS_FEED_CURSOR:-0}
gh issue list --json number,title \
  --jq ".[] | select(.number > $since) | \"#\(.number) \(.title)\"" > /tmp/new.$$
[ -s /tmp/new.$$ ] || exit 1
cat /tmp/new.$$
gh issue list --json number --jq 'map(.number) | max' > "$AEGIS_FEED_NEXT"
```

Items printed without a next cursor are an error, because the next run would
return them again.

## The wake

A batch reaches you as a user turn whose first line is a header:

```text
> from feed:group-chat · batch#42 · 3 items · 2026-11-08T19:40:12Z
```

The body says where the items came from and how long the oldest waited, then
the items in a fenced block, then whether more wait. Act on them and end your
turn; the next batch wakes you.

While you work, nothing is sent into your turn. Your next aegis tool result
says how many items wait, and when your turn ends every feed adds one block to
the messages held for you, however many bursts came meanwhile. What does not
fit one message comes when that next turn ends.

A batch number that arrives marked `again` was sent once already, just before
the server stopped: skip what you already handled.

## Pause, resume and cancel

- `feed_pause` with `name`: the feed keeps reading but wakes nobody;
- `feed_resume` with `name`: it wakes you again, and what came while it was
  paused is the next batch;
- `feed_cancel` with `name`: it is removed, and items still waiting are not
  sent;
- `feed_list`: your feeds, with their state, what waits and what was
  delivered.

Three failed reads in a row (a file that is gone, a command that exits 2)
pause the feed and wake you with the reason. Fix the source and resume it.

## While a feed is live

A live feed will wake you again, so `turn_end` refuses done until you pause or
cancel it. Closing your session cancels its feeds. A `session_handoff` on this
server moves them, cursors included, to the session that continues your work;
a handoff to a linked server is refused while one is live. Feeds are kept in
the server's state and read again after a restart.
