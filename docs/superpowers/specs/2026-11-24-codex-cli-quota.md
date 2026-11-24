# Codex shell CLI and ChatGPT quota (#105)

Status: implemented and validated locally, including real Codex and a fresh browser server.

## Measured problem

With aegis 2.22.0 and Codex 0.164.0, MCP calls authenticate but `aegis call`
in the Codex shell fails because the session token is excluded. The quota
registry has no Codex provider. A real account/rateLimits/read probe returned
one codex bucket with primary windowDurationMins=10080 and secondary=null.

## Decisions

Keep the session token excluded from Codex's shell. Give the CLI the path of
a process-lived credentials file in a 0700 temporary directory, created with
0600 permissions. Read it only as a fallback to the existing environment
credentials. Remove it on spawn failure, exit and stop; mint a new file on
restart. This grants the same session authority to children of the same user
as the existing shell CLI contract; it is not an extra sandbox boundary.
No secret goes on argv or into output.

Register a quota provider whose read_token callback returns a probe executable,
not an OAuth token. Detect the file-backed ChatGPT login and account locally;
ask Codex's authenticated app-server for rate limits without opening a thread
or starting inference. Reuse the existing process lifecycle and quota cache,
backoff, failure and projection machinery. Follow the configured network exit.

Use the codex bucket from rateLimitsByLimitId when available, otherwise the
backward-compatible rateLimits view. Map windows by actual duration, not slot:
300 minutes is session, 10080 minutes weekly_all. Other durations keep their
slot, span and reset. Ignore malformed readings instead of showing invented
zero usage. Do not combine unrelated model buckets. Keyring-only discovery and
per-model bucket display are follow-up work; the help makes these limits explicit.

## Validation

Regression tests exercise a real aegis MCP endpoint from the fake Codex shell,
with token variables removed; they check caller identity, private file modes
and deletion. Quota tests drive the actual Codex process adapter with its fake
JSON-RPC peer and assert the wire snapshot and lack of thread/model requests.
Real Codex live tests cover the shell policy and authenticated quota. A fresh
server and browser verify the quota display at desktop and phone widths.
