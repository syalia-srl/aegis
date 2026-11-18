- **Per-host overrides in one tracked `.aegis.yaml`.** A `hosts:` section holds
  what differs per server, in a block named after each server: its exit, an
  agent's effort, its worker count. Each server applies the shared settings,
  then its own block, so the file is committed and synced through git like any
  other, with no uncommitted edits to step around. Exit here now writes this
  server's block. Before, a git command that reset the file for a moment could
  drop an uncommitted `exit_via:`, and a session started then went out
  directly.
help: config#per-host-overrides
