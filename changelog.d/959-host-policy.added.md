- **A program that embeds aegis decides who may do what.** `Server(root,
  policy=...)` takes a `HostPolicy`, and aegis asks it who a token is, what to
  store as an owner, whether an operation may run, which sessions a caller may
  list, read or change, what a start runs, which aegis tools a harness gets,
  and what happened. `srv.token_for(who)` mints signed tokens for the host's
  page, and `srv.as_who(who)` acts as one of its callers. `aegis serve` runs
  the standalone policy and behaves as before.
help: library#host-policy
