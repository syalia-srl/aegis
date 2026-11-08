- **The client mounts into a host page.** aegis's chat is no longer only the
  standalone page: a Python host mounts `srv.asgi` under its own prefix, mints
  a token with `srv.token_for(who)`, and `mount()` draws one session's
  transcript and composer inside any element it names, with `connection`,
  `entry`, `turnEnd` and `error` events and `send` and `destroy`. With
  `theme: "host"` the mount paints in the page's own CSS variables, and its
  CSS is scoped to `.aegis`, so it does not touch the host's styles; the
  mount's own nodes keep ids (`#composer`, `#input`), so a host styles its own
  boxes by class. One socket serves every mount on the page.
  `/aegis/manifest.json` lists the client's files with a hash each for the
  host's own service worker.
help: library#the-chat-in-your-page
