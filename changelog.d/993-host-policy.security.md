- **A host's policy decides session-less work, and `/mcp` stays off its public
  mount.** A token minted for a person could spawn sessions as them without
  `can()` ever being asked, and a host that mounted `srv.asgi` served the MCP
  tools on its public prefix. Every session-less operation that starts work now
  asks `can(who, "spawn", None)`, the rest ask `can(who, "read"|"write", None)`,
  and only the loopback listener serves `/mcp`.
