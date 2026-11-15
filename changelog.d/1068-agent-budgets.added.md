- **Agents can carry a spending budget.** `budget:` in `.aegis.yaml` takes
  caps in dollars or tokens, per session or per rolling window (`24h`, `7d`,
  `30d`). A session's spend counts for its own agent and for every agent up
  its chain of starters, so a capped agent cannot spend through a queue or a
  child. Past a cap the session gets an aegis notice (`budget:spent:<agent>`)
  telling it to finish. After that turn it starts no turn a person did not
  start until the window has room, which the notice names. A session cap
  never frees. Usage rows now carry the agent, the queue and the starter, and
  `aegis usage --split agent` or `--split queue` groups by them.
help: config#budget-an-agent
