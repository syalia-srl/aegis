// Who this browser is on its server: the welcome names the server's one
// person (tokens.py), whose name a session records as its owner.

export const who = { name: null, owner: "" };

export function setWho(me, owner) {
  who.name = me?.name ?? null;
  who.owner = owner || "";
}

// The owner of session `m`: a session from before owners is the server's.
// Null for one on a linked server, which decides for itself.
export function ownerOf(m) {
  return m.server ? null : m.owner || who.owner || null;
}

// The owner to show beside `m`: someone other than this browser, as a session
// a member of a multi-user server made before aegis 3.0 records.
export function shownOwner(m) {
  const o = ownerOf(m);
  return who.name && o && o !== who.name ? o : null;
}
