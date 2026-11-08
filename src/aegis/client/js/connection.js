// One socket per page, per server URL and token. Every mount asks acquire()
// for the socket it needs; the first creates it and the last release() closes
// it. Two mounts that talk to the same server with the same token therefore
// share one websocket and each subscribes its own channels on it.
//
// The key holds the token itself (or a function's identity, so a host that
// passes the same () => token across mounts shares one socket even though each
// call returns a fresh string). A host that passes different functions gets
// different sockets, which is what its two identities ask for.

import { Connection } from "./protocol.js";

const shared = new Map(); // key -> {conn, users, listeners, last}
const fnKey = new WeakMap(); // token function -> its stable name
let nextFn = 0;

function keyOf(url, token) {
  let t = token;
  if (typeof token === "function") {
    if (!fnKey.has(token)) fnKey.set(token, `fn:${++nextFn}`);
    t = fnKey.get(token);
  }
  return `${url}\u0000${t ?? ""}`;
}

// The socket for `url`, plus the release() that gives it back. `onState` hears
// every connection state; a mount that joins after the socket opened hears the
// last state at once, so it does not sit at "connecting" until the next change.
export function acquire(url, { token = "", onState = () => {} } = {}) {
  const key = keyOf(url, token);
  let held = shared.get(key);
  if (!held) {
    held = { users: 0, listeners: new Set(), last: null, conn: null };
    held.conn = new Connection(url, {
      token,
      onState: (state, server) => {
        held.last = { state, server };
        for (const fn of held.listeners) fn(state, server);
      },
    });
    shared.set(key, held);
    held.conn.connect();
  }
  held.users += 1;
  held.listeners.add(onState);
  if (held.last) onState(held.last.state, held.last.server);
  let released = false;
  return {
    conn: held.conn,
    release() {
      if (released) return; // a double destroy must not free another mount's share
      released = true;
      held.listeners.delete(onState);
      held.users -= 1;
      if (held.users > 0) return;
      held.conn.stop();
      shared.delete(key);
    },
  };
}
