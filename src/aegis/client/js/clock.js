// The server's clock, as this browser can best tell it (#924).
//
// Every timestamp a channel carries (last activity, a monitor's start, a
// plan item's clock) was read on the server. A device whose clock is off,
// such as a phone an hour ahead after its zone missed a daylight saving
// change, would measure every age against them wrongly by that much. The
// welcome carries the server's time, and the offset to it is kept here, so
// "how long ago" is measured on the clock that wrote the stamp.
//
// Durations that start and end on this device (a reconnect countdown, how
// long a draft has been sending) stay on Date.now().

let offset = 0; // seconds to add to this device's clock

/** Take the server's time from a welcome, in epoch seconds. */
export function setServerTime(t) {
  if (typeof t === "number" && Number.isFinite(t)) offset = t - Date.now() / 1000;
}

/** Now on the server's clock, in epoch seconds. */
export const serverNow = () => Date.now() / 1000 + offset;
