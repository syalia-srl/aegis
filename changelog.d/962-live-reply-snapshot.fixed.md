- **A streaming reply no longer disappears when a snapshot lands beside it.**
  A whole transcript snapshot at the revision a tab already held could replace
  what that tab had drawn, taking away a live reply that had grown without
  moving its revision. A snapshot that is not newer than the tab's own data is
  now ignored; only one after a reconnect is authoritative.
