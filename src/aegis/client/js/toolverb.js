// The working row's title while a tool call runs (#1012). With no call live
// the row shows its star and the general verbs; while one runs, and until
// SETTLE after it started so even a quick call is seen, the title becomes
// that call's glyph, pulsing, and a verb of what it does typing out; the
// call's own row names it (#1017). The row sits below the transcript, so it
// shows the call even when its run is folded. The fold names the activity (usage/kinds.py, plus
// debugging after a failed shell command), and entries.js marks a tool row
// with it only while the row runs or is that young.

import { serverNow } from "./clock.js";

const ACTIVITY = {
  reading: "transcript.activity.reading",
  editing: "transcript.activity.editing",
  testing: "transcript.activity.testing",
  debugging: "transcript.activity.debugging",
  server: "transcript.activity.server",
  committing: "transcript.activity.committing",
  web: "transcript.activity.web",
  "other agents": "transcript.activity.agents",
  showing: "transcript.activity.showing",
  bookkeeping: "transcript.activity.bookkeeping",
};
export const SETTLE = 2.8; // seconds: two cycles of the glyph's 1.4 s pulse

// The latest tool row still running or younger than SETTLE; older rows lose
// their mark, so the scan stays short.
function liveCall(root) {
  const now = serverNow();
  let pick = null;
  for (const r of root.querySelectorAll(".row.tool[data-activity]")) {
    if (r.classList.contains("running") || now - Number(r.dataset.at) < SETTLE) pick = r;
    else delete r.dataset.activity;
  }
  return pick && ACTIVITY[pick.dataset.activity] ? pick : null;
}

export class CallTitle {
  constructor() {
    this.id = null; // the call the title shows
    this.activity = null;
    this.star = null; // the working row's own glyph, put back after a call
    this.typer = null;
  }

  // Draw the working row's title from the live call; false when there is
  // none and the caller draws its general verb.
  draw(root, row, t, still) {
    const g = row.querySelector(".g");
    const call = liveCall(root);
    row.classList.toggle("calling", !!call);
    if (!call) {
      if (this.id !== null && this.star) g.replaceChildren(this.star.cloneNode(true));
      this.id = this.activity = null;
      return false;
    }
    if (call.dataset.id !== this.id) {
      if (this.id === null) this.star = g.firstChild?.cloneNode(true) || null;
      const glyph = call.querySelector(".g")?.firstChild;
      if (glyph) g.replaceChildren(glyph.cloneNode(true));
      this.id = call.dataset.id;
    }
    const verbs = t(ACTIVITY[call.dataset.activity]).split(", ");
    if (call.dataset.activity !== this.activity) {
      this.activity = call.dataset.activity;
      this.typer = { verb: Math.floor(Math.random() * verbs.length), pos: 0, phase: "type", wait: 0 };
    }
    const verb = row.querySelector(".wverb > span:first-child");
    const ty = this.typer;
    if (still) {
      if (verb.textContent !== `${verbs[ty.verb]}…`) verb.textContent = `${verbs[ty.verb]}…`;
      return true;
    }
    const word = `${verbs[ty.verb]}…`;
    if (ty.wait > 0) ty.wait -= 1;
    else if (ty.phase === "type") {
      ty.pos += 1;
      if (ty.pos >= word.length) Object.assign(ty, { phase: "erase", wait: 50 });
    } else if (ty.pos > 0) ty.pos -= 1;
    else Object.assign(ty, { phase: "type", verb: (ty.verb + 1) % verbs.length, wait: 4 });
    const text = word.slice(0, ty.pos);
    if (verb.textContent !== text) {
      verb.replaceChildren(
        ...[...text].map((c, i) => {
          const ch = document.createElement("span");
          ch.textContent = c;
          ch.style.animationDelay = `${i * 0.07}s`;
          return ch;
        }),
      );
    }
    return true;
  }
}
