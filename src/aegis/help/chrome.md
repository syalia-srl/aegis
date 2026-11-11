---
title: Your own Chrome
kind: howto
when: pair your own Chrome, Edge or Brave with aegis, share a tab of your browser with an agent, an agent asks for a tab of the person's browser, browser_request
hints:
  - Pair your own Chrome with aegis from Settings → Chrome. #pairing
  - Share a tab of your own browser with an agent, from the extension's popup. #sharing-a-tab
  - An agent can ask for one of your tabs; you give it or refuse. #when-an-agent-asks
---

aegis's extension pairs your own Chromium browser (Chrome, Edge, Brave or
Vivaldi) with your aegis server. Once paired, agents will use the tabs you
share with them, with your sign-ins, cookies and network; the server's own
browsers stay for tests, screenshots and unattended work (browsers). An
agent sees only the tabs you gave it; every other tab, its title and its
address stay in your browser. This version shares tabs; agents driving them
(clicks, typing, snapshots) comes next.

## Installing the extension

In aegis, open Settings → Chrome and press Download the extension. Unzip
`aegis-chrome.zip`, open `chrome://extensions`, turn on Developer mode,
choose Load unpacked and pick the `aegis-chrome` folder. The aegis mark
appears among the browser's extensions; pin it to keep it in the toolbar.

After an update of aegis, download the extension again, replace the folder
and press the reload arrow on its card in `chrome://extensions`.

## Pairing

1. In Settings → Chrome, press Get a pairing code. aegis shows the server's
   address and a code like `K7QF-3MZD`, valid ten minutes and only once.
2. Open the extension's popup, type the server's address and the code, and
   press Pair.
3. The popup shows the server's name and a green dot. Settings → Chrome lists
   the browser as connected, and the Fleet shows it as a card.

The extension gets a device token of its own. It opens only the extension's
connection to the server, never the page or any operation, and the server
keeps only its hash. The popup draws in the theme you picked in aegis and
keeps it while it is offline.

## Connected or offline

The dot is green while the extension's connection is open. A browser that
closes or a laptop that sleeps shows as offline with the time it was last
seen, and reconnects by itself within a minute of coming back.

## Unpairing

Press Unpair on the browser's row in Settings → Chrome, or Unpair in the
popup. Its token stops working at once and its connection closes; pairing
again needs a new code.

## Sharing a tab

Open the popup. Under This window, each tab has a switch. Turn one on, tick
the agent sessions that get it and press Share. The tab joins the browser's
tab group named aegis, and its row shows a chip per agent and its name for
agents, such as `chrome:github`. The toolbar mark turns to the accent while a
tab is shared. Add an agent adds another session to the same tab.

## When an agent asks

An agent asks for one of your tabs with `browser_request`, or to open a page
in a new tab with `browser_open` and `where="person"`. A card with Give and
Refuse appears under the agent's call, and the extension's icon gets a badge; the
popup shows the same request with the tabs that match it. Answer in either
place: the other one follows. Give on the card in aegis gives the tab that
matches best, or opens the page; pick another tab in the popup. The agent is
woken with granted, naming the tab, or denied, and never learns which other
tabs you have.

## Revoking

Each chip's × in the popup disconnects one agent, and turning the switch off
stops sharing the tab. In aegis, the session's side panel lists its tabs
under Your browser, each with Disconnect. Closing the tab ends every grant on
it. Grants last as long as the tab: a browser that goes offline keeps them
and gives them back when it reconnects.

## For agents

`browser_list` returns every browser you may use: the server's, kind
`server`, and the tabs the person gave you, kind `person`, with an id like
`chrome:github`. `browser_request(match, why)` asks for an existing tab:
`match` is words or part of its address (`qwen.ai`, `the pull request I have
open`), `why` is shown to the person. End your turn after it; you are woken
with `> from chrome:<name> · granted` or `> from chrome · denied`.
`browser_open(url, where="person", why)` asks to open the page in a new tab
of the person's browser, with the same answer. Both fail with
the error chrome_unavailable when no browser is connected: wait, or use a server
browser if the task allows it.
