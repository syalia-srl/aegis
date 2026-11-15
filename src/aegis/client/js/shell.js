// The aegis Android app around this page (android/, devices.py). The app
// injects window.aegisShell only for this server's origin; it answers JSON
// messages that carry the id of the request they answer. Outside the app
// inShell() is false and nothing here runs.

const pending = new Map();
let next = 0;

function port() {
  return globalThis.aegisShell || null;
}

export function inShell() {
  return port() !== null;
}

export function onAndroid() {
  return /Android/i.test(navigator.userAgent);
}

// Ask the app: info (its version and whether this phone is paired), pair, or
// openDeviceSettings. Rejects after 30 s, or with the app's error.
export function ask(method) {
  const p = port();
  if (!p) return Promise.reject(new Error("not inside the aegis app"));
  if (!p.onmessage) {
    p.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      const w = pending.get(msg.id);
      if (!w) return;
      pending.delete(msg.id);
      clearTimeout(w.timer);
      if (msg.error) w.reject(new Error(msg.error));
      else w.resolve(msg.result);
    };
  }
  const id = ++next;
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(new Error("the aegis app did not answer"));
    }, 30_000);
    pending.set(id, { resolve, reject, timer });
    p.postMessage(JSON.stringify({ id, method }));
  });
}
