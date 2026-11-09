- **A page embeds the whole client, not only its chat.** `mount()` draws all
  three layouts — one session's chat, a tab strip, or the standalone client —
  into an element the host owns, with `sidebar` and `page` deciding whether a
  full mount brings the session panel or owns the document. A host's own tool
  draws its card with `renderers`, its own speech service replaces the
  browser's with `transcribe`, and the handle gains `sessionChanged`,
  `turnStart` and `fileSent` plus `open`, `spawn` and `focus`, so a page
  follows a session, starts one and reacts to a turn without folding aegis's
  events by hand. The options, events, calls, stable classes, prefix, token and
  manifest are on one page, with a Python host and a reverse proxy as examples.
help: embedding
