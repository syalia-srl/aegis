- **pi runs as a harness.** Set `harness: pi` on an agent to run
  [pi](https://pi.dev) 1.5.0 over its RPC mode, with any provider/model pi knows.
  aegis's tools reach it through pi's own MCP client under the same names as in
  Claude Code; a message sent mid-turn is steered into the running turn, a stop
  aborts it (a running command included), and the card shows cost and context.
help: config#add-an-agent
