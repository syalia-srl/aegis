---
title: Embedding the client
kind: howto
when: embedding aegis's chat, tabs or full client in your own web page, and mounting it under your own URL prefix with your own token, theme and tool cards
hints:
  - Mount aegis's chat in any element of your page. #mount-the-client
  - Draw your own card for one of your tools. #renderers
  - Serve aegis under your own URL prefix. #serving-under-a-prefix
---

aegis's web client is not one page: it is a set of mounts. Your page names an
element and a session, and `mount()` draws aegis inside that element, in your
colours, under your own prefix and sign-in. Nothing the mount draws reaches an
id or a selector outside the element you gave it, so your page keeps its own
boxes and its own layout. This page is the whole contract: the options, the
layouts, the events and calls, the classes you may style, and what you serve
and how you sign the browser in.

## Mount the client

Serve the client where your page imports it, then import `mount` from
`<prefix>/aegis.js` and call it once per element:

```html
<div id="chat"></div>
<script type="module">
  import { mount } from "/aegis/aegis.js";
  const chat = mount(document.getElementById("chat"), {
    token: () => fetch("/api/aegis-token").then((r) => r.text()),
    session: "20261106-070453-2f96a5",
    layout: "chat",
    theme: "host",
    lang: "es",
  });
  chat.on("turnEnd", ({ cost_usd }) => reloadNotebook(cost_usd));
  await chat.send("Resume el capítulo 3");
</script>
```

The mount builds one `.aegis` root inside your element and returns a small
handle: `on` for events, `send`, `open`, `spawn`, `focus` and `destroy`, and
`el`, the root it built. The element needs a height from your CSS — aegis does
not stretch your page — and two mounts on one page share one socket when they
pass the same server and the same token, or the same token function.

Every word the client shows comes from a label table. `lang` picks `"en"` or
`"es"`, `labels` replaces single keys, and a key a table lacks falls back to
English and is logged once by name. The standalone page takes the same choice
in its URL as `?lang=es`.

## Layouts

| Layout | What it draws | Element it wants |
|---|---|---|
| `"chat"` | One session's transcript and, unless `composer` is false, its message box. | A box with a height, as tall as you want the conversation. |
| `"tabs"` | A strip of the sessions the policy lists for this caller, with a button that starts a new one, over the same transcript and box. | The same box; aegis scrolls only the strip. |
| `"full"` | The whole standalone client: the Fleet, the other screens, the session panel, the canvas that docks files and pages, the router and the keys. | A box that fills the area you give it; the layout is a grid. |

A chat and a tabs mount each own only their root and the nodes inside it.
`full` is the standalone page's own layout, built into your element; it keeps
its router and key table inside the mount and never reads or writes your URL
hash. `sidebar: false` gives a full mount only the transcript, without the
session panel, for a host that wants the conversation alone. `page: true` is
only for the standalone page — it lets the mount own the document title,
favicon, theme attribute, URL hash, service worker and push. Leave it false in
your page.

## Options

| Option | Type and default | What it does |
|---|---|---|
| `token` | string or `() => string \| Promise<string>`, `""` | Sent in the hello on each connect. A function is called every time, so an expired token is replaced without a reload. |
| `layout` | `"chat" \| "tabs" \| "full"`, `"chat"` | Which of the layouts above to build. |
| `session` | string or null, `null` | The session the mount follows first, by log id or `server/log_id` key. A chat with no session and an `agent` starts one on the first send. |
| `agent` | string or null, `null` | The agent a first send starts a session from, and the default for `spawn()`. |
| `sidebar` | boolean, `layout === "full"` | Full only: builds the session panel unless false. Ignored by chat and tabs. |
| `composer` | boolean, `true` | Draws the message box. False is a transcript only, as for an answer pane. |
| `controls` | boolean, `true` | Draws the model, effort and permission chips under the box. False hides operator details from a product's end users. |
| `dictation` | boolean, `false` | Builds the mic and hold-to-record beside the box. |
| `commands` | boolean, `false` | Builds the `/` command menu under the box. |
| `attachments` | boolean, `true` | Builds the clip button and accepts paste and drop. False builds no clip and wires neither, so your own handlers get them; a dictation take still needs the tray, so a chat that takes neither sets `dictation` false too. |
| `transcribe` | `(audio: Float32Array) => Promise<string>`, `null` | Replaces the browser's speech engine. See [Dictation and transcribe](#dictation-and-transcribe). |
| `theme` | string, `"ink"` | A built-in theme's name, or `"host"` to load no theme and read your page's CSS variables. See [Theming and the stable classes](#theming-and-the-stable-classes). |
| `lang` | `"en" \| "es"`, `"en"` | Picks the label table; an unknown language falls back to English. |
| `labels` | `Record<string, string>`, `{}` | Replaces single keys of the chosen table. |
| `renderers` | `Record<string, (entry, el) => boolean>`, `{}` | Draws your own card for a tool call. See [Renderers](#renderers). |
| `page` | boolean, `false` | Full only, and true only for the standalone page. False keeps your title, hash, theme, worker and push. |

The standalone page passes two more options of its own, `adopt` and `outbox`;
they are not part of a host's contract.

## Events

`on(name, fn)` adds a listener and returns the handle, so calls chain. A
listener that throws is logged and does not stop the others. A mount and its
listeners live until `destroy()`.

| Event | Payload | When |
|---|---|---|
| `connection` | `{state, server}`: `connecting`, `open`, `closed`, `unauthorized` or `version` | Every socket state change, and once to a listener that joins after the socket opened. |
| `sessionChanged` | `{session, previous, meta}` | The session this mount shows changed: `open()`, a tab click, a spawn, or the session going away. `meta` is the sessions-channel row, or null before it arrives. |
| `turnStart` | `{session, meta}` | A followed session's row enters working in a patch this mount saw. A session first heard of already working fires none: nothing is invented about a turn the mount joined halfway. |
| `turnEnd` | `{session, cost_usd, line, meta}` | It leaves working. The payload's cost is this turn's own (the cumulative figure after minus the one before, null when the mount joined mid-turn); `line` is the attention line when the turn left one, else null. |
| `entry` | `{session, entry}` | Every transcript upsert of the shown session, history included. |
| `fileSent` | `{session, entry, files}` | A live file entry upserts: an agent's file_send, with the files it sent — a screenshot, a rendered PDF, a draft. `entry` fires too; this is the narrow event a host can react to without inspecting kinds. History replayed by a snapshot does not fire it. |
| `error` | `{session, code, message, error}` | A call the mount made was refused, or its transcript channel failed. `error` is the client's error object for a program that wants its data. |

A chat reports turns for the session it shows, so one chat per host tab does
not hear every turn on the server. A full mount shows every session the caller
may see and reports turns for all of them. `entry` and `fileSent` are for the
transcript in front of the mount.

## Calls

| Call | Returns | What it does |
|---|---|---|
| `send(text)` | `Promise<void>` | The box's own send, through this mount's socket; resolves when the send has an answer. With no session and an `agent`, it starts one first and sends there. |
| `open(session)` | the handle | Shows that session, by log id or `server/log_id` key, and emits `sessionChanged` when it differs. |
| `spawn(agent?, prompt?)` | `Promise<{session, handle}>` | Starts a session through session.spawn (the policy decides), opens it, and sends `prompt` as its first message when given. A refusal emits `error` and rejects. The agent defaults to `agent`. |
| `focus()` | nothing | Puts the caret in this mount's box, or focuses the transcript when it has none. |
| `destroy()` | nothing | Unsubscribes, tears a full layout's controllers down, gives the socket back and removes the root. It is idempotent. |
| `on(name, fn)` | the handle | Adds an event listener. |

A full mount builds its markup at once and loads its behaviour just after, so
`on` works immediately and `open`, `spawn`, `send` and `focus` wait in call
order until it is composed. `destroy()` before that happens composes nothing.

## Renderers

`renderers` maps a tool's folded name to `(entry, el) => boolean`. aegis calls
it when that call is no longer running: fill `el` with your card and return
true, or return false to keep aegis's own row. It runs again on every update of
that row, so draw from the entry it is given and keep no state; a throw is
logged and leaves aegis's row.

The key is the name the server folded: `file_send` for an aegis tool, and
another MCP server's full harness name, such as `mcp__life__send_card`, for its
own. The entry is the wire's tool entry: `title` is that name, `summary`,
`status`, `glyph` and `detail` are what the server computed. A host whose tool
should show something richer can also have the tool return an artifact, which
aegis already draws in the transcript.

```javascript
const chat = mount(document.getElementById("chat"), {
  session: "20261106-070453-2f96a5",
  renderers: {
    mcp__life__send_card: (entry, el) => {
      if (entry.status !== "done") return false;
      el.append(cardFor(entry.detail.args));
      return true;
    },
  },
});
```

## Dictation and transcribe

`dictation: true` builds the mic and hold-to-record beside the box. Without a
`transcribe` hook, a take runs a speech model in the browser. With one, each
piece of the take is handed to your service instead: 16 kHz mono audio as a
`Float32Array`, and its returned text lands where the local engine's would. No
model is downloaded when the hook is set.

```javascript
transcribe: async (audio) => {
  const body = new Blob([audio.buffer], { type: "application/octet-stream" });
  const r = await fetch("/api/speech", { method: "POST", body });
  return r.ok ? (await r.json()).text : "";
},
```

A take is uploaded through the composer's attachment tray, so leaving
`attachments` on is what lets it reach the session; a chat that takes neither
dictation nor files sets both off.

## Theming and the stable classes

`theme` is `"host"`, or a built-in theme's name such as `"ink"` or `"riso"`,
which aegis loads itself. With `theme: "host"` aegis loads no theme: it paints
with the CSS variables your page sets, and your CSS is the contract. Set them
on the mount's ancestors, usually `:root`; anything unset keeps a plain
fallback.

| Group | Variables |
|---|---|
| Background and panels | `--bg`, `--surface`, `--raised`, `--chrome`, `--side` |
| Text | `--ink`, `--strong`, `--muted`, `--faint` |
| Lines and accents | `--rule`, `--accent`, `--accent-soft`, `--on-accent` |
| Statuses | `--ok`, `--err`, `--err-bg`, `--warn`, `--comms`, `--add-bg`, `--del-bg` |
| Syntax colours | `--h-kw`, `--h-str`, `--h-num`, `--h-com`, `--h-fn`, `--h-type`, `--h-var`, `--h-meta` |
| Fonts | `--font-ui`, `--font-prose`, `--font-mono`, `--font-chrome`, `--font-head` |
| Shape and row | `--prose-size`, `--r`, `--r-lg`, `--time`, `--thread`, `--row-cols`, `--fill` |

These classes inside the mount are stable across minor releases, so you may
select and restyle them. The root also carries `data-layout`, `data-theme` and
`data-page`. Anything else is private.

| Group | Classes |
|---|---|
| Root and layout | `.aegis`, `.aegis[data-layout]`, `.aegis[data-theme]`, `.aegis-chat` |
| Tabs layout | `.aegis-tabs`, `.aegis-tabstrip`, `.aegis-tab`, `.aegis-tab.on`, `.aegis-tab-new`, `.aegis-pane` |
| Transcript | `.tr-pane`, `.tr`, `.entries`, `.row`, `.row.user`, `.row.prose`, `.row.tool`, `.row.file`, `.row.artifact`, `.row.sys`, `.row.error`, `.body`, `.fcard`, `.viewer` |
| Composer | `.composer`, `.box`, `.atts`, `.chips`, `.chip`, `.send`, `.mic` |
| Transcript chrome | `.nav`, `.find` |

Hiding tool calls is one rule of yours:

```css
#chat .aegis .tool { display: none }
```

It ties with the client's own `.aegis .row` rule, so put your block in the
page's body — the client's stylesheets load into its head — or name the element
you gave `mount()`, as above. The mount's own nodes keep ids such as
`#composer`, `#input` and `#entries`, so a page with its own `#composer` has
two: `getElementById` still returns yours, and an id selector matches both.
Style your own boxes by class, or from the element you gave `mount()`.

## Serving under a prefix

A Python host runs a `Server` and mounts `srv.asgi` into its own web app at the
prefix the browser sees. Its routes are relative, so the mount adds nothing to
them; every URL aegis generates — the client's files, the websocket, the
manifest — carries the prefix, and the page configures nothing.

```python
from starlette.applications import Starlette
from starlette.responses import HTMLResponse
from starlette.routing import Mount, Route

async with Server("./proj", prefix="/aegis", policy=MyPolicy(),
                  origins=["https://app.example.com"]) as srv:
    token = srv.token_for(ana)        # whose the page acts as

    async def index(request):
        return srv.sign_in(HTMLResponse(PAGE.replace("__TOKEN__", token)), token)

    app = Starlette(routes=[Route("/", index), Mount("/aegis", srv.asgi)])
```

The loopback listener keeps serving the MCP tools at the app's root; the app
you mount does not serve MCP at all, so your public origin never exposes the
tools harness children use. A prefixed server's own page is not browsable; your
page serves the client. The websocket's origin check admits your page: pass its
public origin in `origins`.

Any other host proxies the prefix to the loopback port and strips it there.
Pass `prefix=` exactly what the browser sees: aegis's routes are relative, and
everything it hands the page carries that prefix. Keep the upgrade headers for
the socket:

```nginx
location /aegis/ {
    proxy_pass http://127.0.0.1:8420/;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
}
```

Everything aegis hands the page is a URL under that prefix, so a host that
rewrites, terminates TLS or serves from a subpath needs no other change. A
prefixed client on an origin that is not its own must have that origin in
`origins`, or the socket is refused.

## Tokens and sign-in

`srv.token_for(who, ttl=3600)` mints a short-lived signed token for one of your
policy's callers: it holds the caller's key and an expiry, needs no table, and
survives a restart until it expires. Your page passes it to `mount()`, the hello
carries it, and your `identify` never sees it. A function as `token` is called
on each connect, so the page can fetch a fresh one and an expired token is
replaced without a reload.

Set the same token as the page's cookie with `srv.sign_in(response, token,
request)`: a websocket hello carries the token, but an HTTP fetch of a sent
file, an image or a preview cannot. The cookie is HttpOnly and SameSite strict;
pass the request, and it is `Secure` when the browser reached you over https.
Because a minted token expires, a host that keeps its page open mints another
and signs the browser in again with it.

## The manifest and offline

`<prefix>/manifest.json` lists every client file with its URL, byte count and
hash, plus `build`, `version` and `prefix`, and the public entry at
`<prefix>/aegis.js`, which the static directory does not serve. Your own service
worker caches those files and compares `build` to know when the client changed.
aegis installs no service worker in an embed: the worker is yours, and so is
the offline policy.

A mounted chat works with no connection, which is what a phone in a WebView or
a notebook with the laptop shut needs. A message typed while the socket is down
stays on the device as that session's draft and draws as the transcript's
queued row; the reconnect sends it once, never twice. A draft written before
the session moved on is not sent blind: the queued row says so and offers
`Send anyway`, so the person decides. Cache the files the manifest lists, and
the page's own last transcript, and the chat opens with no network at all.
