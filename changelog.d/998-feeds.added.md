- **Feeds wake a session with each batch of new items.** A session that had to
  act on a growing source, a chat log appended to a file or a command's new
  output, kept its own count of what it had read and re-armed a one-shot
  monitor after every wake; a turn that forgot missed items, and a line written
  between its read and its count was lost. `feed_start` binds a file (with an
  optional `jq` filter for jsonl) or a command with a cursor to the session;
  aegis keeps the cursor, debounces bursts, sends what came during a turn as
  one block when it ends, and keeps the feed until it is cancelled or the
  session closes.
help: feeds
