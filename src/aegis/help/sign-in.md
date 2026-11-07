---
title: Harness sign-in
kind: howto
when: a session fails to sign in (a 401, "Not logged in"), Claude Code sessions keep getting logged out, or a harness needs a long-lived token
hints:
  - A failed Claude sign-in opens a card that runs claude setup-token for you. #when-sign-in-fails
  - Give every Claude session a token that lasts a year, with no logouts. #sign-in-for-a-year
---

Sessions in aegis sign in with your own subscription, through each harness's
own login. This page says what aegis does when a session fails to sign in, how
to keep Claude Code sessions signed in for a year, and how the quota gauges
read your usage.

## Why sessions get logged out

Claude Code's stored sign-in (from Claude Code's /login) holds an access
token that lasts about eight hours and a refresh token that works once. When
the access token expires, every running Claude Code process tries to refresh
it. The first one succeeds, the old refresh token stops working, and the
others fail with a 401. The stored sign-in can end up revoked, so every
session fails until someone signs in again. An aegis server runs many
sessions at once, so it meets this often.

## Renew from Settings or by an agent

**Settings → Harnesses** lists each harness aegis can sign in: whether it is
installed, its version, and, when aegis keeps a token for it, until when.
**Sign in** opens the same card in the session you last looked at, without
waiting for a turn to fail.

An agent can open the card with the `signin_open` tool, for example to renew
before the token expires. Only you can answer the card; the agent is woken
with `from aegis:login · renewed` when the sign-in is done.

## Sign in for a year

A token from Claude Code's setup-token command lasts a year and is never
refreshed, so no two sessions race over it. aegis gives it to every Claude
Code process it starts as the server's own user, as `CLAUDE_CODE_OAUTH_TOKEN`:
sessions, recaps and side notes.

1. On any machine with a browser, run `claude setup-token`.
2. Approve access in the browser. If the browser shows a code (two parts
   joined by `#`), paste it back into that terminal; it is not the token.
3. The terminal prints a line starting with `sk-ant-oat01-`. That is the
   token. Paste it into the sign-in card's **Paste a token instead** (below), or write it to
   `~/.config/aegis/claude-oauth-token` on the server, mode 0600.

aegis reads the file each time a process starts, so a new token applies to
the next session without a restart. A `CLAUDE_CODE_OAUTH_TOKEN` already in the
server's environment wins over the file.

The token can only make model requests. Claude Code's Remote Control,
claude.ai connectors and /schedule do not work with it; MCP servers you
configure yourself do. Without the file, sessions use the stored sign-in as
before.

`aegis doctor` reports the file as `claude.token`: an error when it is not
mode 0600 or owned by the server's user, a warning in the fourteen days
before the token expires, about a year after the file was written.

## When sign-in fails

When a turn fails to sign in (a 401, or "Not logged in"), every browser gets a
toast and the session's transcript shows a sign-in card. aegis runs the
harness's own login command on the server for you; for Claude that is
`claude setup-token`. Sessions that fail while the card is open join its list
instead of opening another.

1. Press **Open sign-in page** and approve access in your browser.
2. The page shows a code. Paste it into the card and press **Sign in**.

aegis types the code into the login, reads the token it prints, and checks it
with one short request on haiku. When Claude Code accepts it, the token is
written to the file above, every session on the list restarts and is told to
continue, and the card reads "Signed in".

A wrong or expired code, **Cancel**, ten minutes without an answer, or a screen
aegis does not recognise ends the card as failed with the reason and the
login's last lines. The sessions keep waiting: **Start again** opens a new
card for them. A server restart ends an open card the same way.

For OpenCode the card runs `opencode auth login` and asks which subscription:
**OpenCode Go** asks for your API key, which aegis types into the login and
then tries with one short turn on the first OpenCode agent's model; **OpenCode
Console** shows a code and a link where you approve it. A turn that OpenCode's
provider refuses (a wrong or revoked key) opens the card by itself.

**Show terminal** opens the terminal the login runs in, to see what it shows.
That terminal keeps nothing on disk, and any token on its screen is replaced
with `••••` before it is shown or kept. What you type on the card goes to the
login only: never into a transcript, a log or an agent's context. An agent can
see that a card is open but can never answer it.

**Paste a token instead** takes a token you made with `claude setup-token` on
another machine, as in the steps above.

## Quota with a long-lived token

The Claude gauges read your usage from an endpoint that needs a scope the
setup-token lacks, so it refuses it. Each turn of a Claude session also
reports the five-hour and weekly windows, and the gauges take those readings
when the endpoint refuses. They update as sessions run and keep their last
reading while none does; the weekly Opus window shows only while the
endpoint works.
