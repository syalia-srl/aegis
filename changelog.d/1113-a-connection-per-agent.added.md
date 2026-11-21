- **A Claude Code agent can run on an account of its own.** `base_url:`,
  `pool:`, `api_key_file:` and a restricted `env:` start its sessions on an
  endpoint that speaks Anthropic's API — OpenCode Go's among them — without the
  Claude sign-in token in the process, with the key read from the pool's own
  meter or a 0600 file, and with the Claude-only paths left alone. The record
  keeps the key's path, never the key, and a restart keeps the endpoint an old
  session started on. `aegis doctor` reports a connection without comparing it
  to Claude's catalog, and `--probe <agent>` spends one call to prove it works.
help: config
