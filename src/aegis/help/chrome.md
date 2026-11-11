---
title: Your own Chrome
kind: howto
when: pair your own Chrome, Edge or Brave with aegis, install the aegis browser extension, unpair a browser
hints:
  - Pair your own Chrome with aegis from Settings → Chrome. #pairing
---

aegis's extension pairs your own Chromium browser (Chrome, Edge, Brave or
Vivaldi) with your aegis server. Once paired, agents will use the tabs you
share with them, with your sign-ins, cookies and network; the server's own
browsers stay for tests, screenshots and unattended work (browsers). This
version pairs the browser and shows it in aegis; sharing tabs comes next.

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
