- **Queues share one worker limit by priority.** `max_workers:` bounds the
  queue workers on the server, every queue together (4 when left out), and
  each queue's `priority:` sets its share: a queue at 2 runs two workers for
  each one at 1, and 0 pauses it while its tasks wait. A queue's own
  `max_parallel:` is ignored and `aegis doctor` warns about it, so the total
  is one number instead of a sum kept in step by hand.
help: queues#priorities-and-the-worker-limit
