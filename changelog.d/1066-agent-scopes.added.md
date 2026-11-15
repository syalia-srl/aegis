- **An agent preset can fence the aegis tools, queues and agents its sessions
  use.** `tools:` in `.aegis.yaml` takes `allow` or `deny` over tool names and
  groups (spawn, queues, workflows, schedules, browser, secrets, config, help),
  plus `queues:` and `agents:` lists. A scoped session lists only its tools,
  its workplace prompt leaves out the rest, and a call outside the scope, as a
  tool or through `aegis call`, is refused with out_of_scope. The scope is
  fixed at spawn and met with the starter's, so a worker or a spawned session
  never gets more than the session that started it. Before, every session got
  every aegis tool.
help: config#scope-an-agent
