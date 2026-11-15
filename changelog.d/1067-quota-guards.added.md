- **Agents and queues can carry a quota guard.** `quota:` in `.aegis.yaml`
  takes the lines a schedule's quota takes, now also as a map of `used`,
  `projected` and `stop`, which schedules accept too. The gate a turn or a
  worker passes is the most restrictive of its agent's, its queue's and the
  guards of the agents that started it, and a stop line cuts running turns
  until the window is back under it. A queue's `on_closed` rejects with
  queue_closed, warns or holds, and `max_pending:` refuses with queue_full.
  An agent's `on_closed: refuse` holds wakes, skips schedule runs and refuses
  handoffs; under `warn`, and for a person's message, the turn starts with an
  aegis notice saying what is closed, which queues reject, and when it opens.
  Before, only schedules were ever held on the quota.
help: config#guard-an-agent-or-a-queue
