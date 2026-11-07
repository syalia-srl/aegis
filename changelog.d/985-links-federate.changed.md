- **Every link goes both ways, and agents act across it as at home.** A link
  now joins two servers: the far server reaches back over the same socket, and
  agents on each start, read, hand off to, wait on and close the other's
  sessions, enqueue on its queues and use its schedules, as only a trusted link
  allowed before. A far server's error text reaches the agent, and any link can
  be an exit. Both servers need aegis 3.0.0, and a pair of servers keeps one
  link: a second one is refused and says why.
help: links#what-agents-can-do-across-a-link
