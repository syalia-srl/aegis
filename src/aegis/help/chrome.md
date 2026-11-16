---
title: Your own Chrome
kind: howto
when: pair your own Chrome, Edge or Brave with aegis, share a tab of your browser with an agent, an agent asks for a tab of the person's browser, an agent drives a tab of the person's browser, browser_request
hints:
  - Pair your Chrome in Settings → Chrome, then share a tab from the extension's popup. #sharing-a-tab
  - An agent can ask for one of your tabs; you give it or refuse. #when-an-agent-asks
  - An agent drives a tab you gave it, and hands it to you when it needs you. #an-agent-at-work
---

aegis's extension pairs your own Chromium browser (Chrome, Edge, Brave or
Vivaldi) with your aegis server. Once paired, agents will use the tabs you
share with them, with your sign-ins, cookies and network; the server's own
browsers stay for tests, screenshots and unattended work (browsers). An
agent sees only the tabs you gave it; every other tab, its title and its
address stay in your browser. An agent reads a tab you gave it, clicks,
types and navigates in it with the same tools it uses on a server browser.

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
place: the other one follows. Give on the card in aegis gives the tab whose
title or address matches best, or opens the page. When no tab matches,
nothing is given: aegis says so, and you pick the tab in the popup. Pick a
tab in the popup to choose it yourself. The agent is
woken with granted, naming the tab, or denied, and never learns which other
tabs you have.

## Revoking

Each chip's × in the popup disconnects one agent, and turning the switch off
stops sharing the tab. In aegis, the session's side panel lists its tabs
under Your browser; click it for each tab's title, address and the other
agents on it, with Disconnect and Stop sharing, and for the requests still
waiting, with Give and Refuse. Closing the tab ends every grant on
it. Grants last as long as the tab: a browser that goes offline keeps them
and gives them back when it reconnects.

## An agent at work

While a tab has a grant, the extension holds Chrome's debugger on it, and
Chrome shows its bar saying the extension is debugging the browser. That
is how the agent's clicks and keys reach the page as yours would. The bar
goes when the last grant on the tab ends.

A thin ribbon at the top of the page names the agents that have the tab.
While one acts, the ribbon says what it does, such as "clicks Send", and a
ring in the accent marks the element it touches. The ribbon takes no
clicks, and the page cannot see or change it.

The session's side panel shows each tab's last action under Your browser.
Typing into a password field is logged as its length, never its text.

When several agents share a tab, their actions on it run one at a time, in
the order they arrive; reading the page never waits.

## Letting agents run scripts

An agent can run JavaScript in a tab you shared with `browser_eval`, but only
while that tab's Scripts switch is on. It is off for every new tab. Turn it
on with Scripts under the tab's chips in the popup, or with Scripts on the
tab in the session's side panel; either one turns it off again, and it ends
with the tab's last grant. A script runs with your sign-ins and can read or
change anything the site lets you, not only what the page shows, so turn it
on for a tab and a task, and off after.

## Pausing and handing over

Pause in the ribbon makes the tab yours: agents can still read it, but any
click, typing or navigation they try is refused until you press Resume, and
the ones that tried are woken then.

An agent that needs you on the page (a captcha, a sign-in, a step to
confirm) hands it over with a reason. The ribbon turns red with the reason
and a Done button, the browser shows a notification with the aegis mark,
and aegis shows a toast. A click on the notification brings the tab to the
front. Do what it asks, then press Done in the ribbon, or Done on the tab
in the session's side panel. The agent is woken with what the page did
meanwhile.

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

On a tab you were given, pass its id as `browser`: `browser_snapshot`,
`browser_click`, `browser_type`, `browser_press`, `browser_goto`,
`browser_screenshot` and `browser_events` work as on a server browser, and
targets are written the same way (`role=button[name="Send"]`, `text=Sign
in`, `label=Email`, or CSS). `browser_goto` loads only http and https
addresses there. `browser_handover(browser, reason)` gives the tab to the
person with your reason; end your turn, and you are woken with
`> from chrome:<name> · back` when they press Done. While the person holds
a tab, after a handover or a pause, reads work and actions fail with
person_has_control: end your turn, and you are woken when it comes back.
`browser_eval(browser, expression)` runs JavaScript in the page and returns
its JSON value, only while the person has turned Scripts on for that tab;
otherwise it fails with scripts_off, and you ask them to turn it on.
A call the person's extension is too old for fails with
extension_outdated: ask them to download it again from Settings → Chrome
and reload it.
`browser_resize` and `browser_close` do not work on the person's tabs yet. A call on a tab that
was never yours fails with not_granted; on one whose grant ended (the person
stopped sharing it, closed it or disconnected you) with revoked.
