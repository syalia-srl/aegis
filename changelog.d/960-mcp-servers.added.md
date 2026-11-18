- **An agent's sessions get the MCP servers it names.** `.aegis.yaml` takes an
  `mcp:` map of HTTP and stdio servers, and an agent attaches them with its own
  `mcp:` list. Every harness gets them beside aegis's own, with `${VAR}` read
  from aegis's environment and no header or env value in a command line or a
  file. A program adds servers with `Server(mcp=...)` and the host policy's
  `mcp`. Settings adds, edits and tests them, and `aegis doctor` checks them.
help: config#mcp-servers
