// The session panel: the card the session's title and facts live in, its plan,
// the skills it loaded, the repos it wrote to, today's journal, what it waits
// on, its monitors, usage, quota, the host, the session actions, the
// version/update card and the phone drawer.
//
// The part builds the markup index.html used to hold (the grip and the aside,
// which the session view's grid addresses as its own children), finds every
// node under the aside it built, and never asks the page for an id. Everything
// it cannot know — which session is in front, how to open or close one, where
// a view goes, how the host's network is measured, how the header's version
// line is drawn — arrives as `actions` from app.js until the full layout owns
// them (b8).
//
// The panel follows the session the page renders: render(s) fills the card and
// its rows, drawQuota/drawHost answer the page's quota and host channels, and
// tickPlan moves the clocks. The drawer, the collapse and the width are the
// Side in side.js, one per part.

import { serverNow } from "../clock.js";
import { ask } from "../dialog.js";
import { byId } from "../dom.js";
import { ago, modelLine, money } from "../fleet.js";
import {
  age,
  countdown,
  diskRows,
  elapsed,
  hostRow,
  hostSeverity,
  mbps,
  netRows,
  providerFor,
  quotaSideRow,
  quotaTile,
  tile,
} from "../gauges.js";
import { entryIcon, factIcon, glyph, icon, iconText } from "../glyphs.js";
import { entry as journalEntry, opens } from "../journal.js";
import { renderFeeds } from "../feeds.js";
import { renderMonitors } from "../monitors.js";
import { dur, planTimes } from "../plantime.js";
import { applyLabels, labelsFor, stateLabel } from "../labels.js";
import { Side } from "../side.js";

// The markup this part owns. The ids stay as they are: they are the browser
// suite's hooks, and two mounts' copies are harmless because every lookup is
// scoped to the part's own element.
const SIDEBAR_HTML = `
<div class="sec"><h4 data-t="sidebar.session"></h4>
  <div class="prow" id="p-session" tabindex="0">
    <div class="title editable" id="s-title" data-t-title="sidebar.renameTitle"></div>
    <div class="sline"><span class="handle editable" id="s-handle" data-t-title="sidebar.renameTitle"></span><span class="at" id="s-at" data-t-title="sidebar.serverTitle"></span><span class="badge" id="s-worker" hidden></span><span class="st" id="s-status"></span></div>
    <div class="pcard">
      <div class="chd"><div class="t" id="c-title"></div><div class="sub" id="c-sub"></div></div>
      <div class="csec">
        <div class="kv"><span data-t="sidebar.kv.harness"></span><span id="s-harness"></span></div>
        <div class="kv"><span data-t="sidebar.kv.model"></span><span id="s-model"></span></div>
        <div class="kv"><span data-t="sidebar.kv.effort"></span><span id="s-effort"></span></div>
        <div class="kv"><span data-t="sidebar.kv.permission"></span><span id="s-perm"></span></div>
        <div class="kv"><span data-t="sidebar.kv.directory"></span><span class="path" id="s-cwd"></span></div>
        <div class="kv"><span data-t="sidebar.kv.server"></span><span id="s-server"></span></div>
        <div class="kv" id="s-exit-row" hidden><span data-t="sidebar.exitsVia"></span><span id="s-exit"></span></div>
        <p class="cnote" data-t="sidebar.renameNote"></p>
      </div>
    </div>
  </div>
  <div class="askbox" id="s-ask" hidden></div>
</div>
<div class="sec" id="s-plan-sec" hidden><h4 id="s-plan-h" data-t="sidebar.plan"></h4>
  <div class="plan" id="s-plan"></div>
  <div class="prow more" id="p-plan" tabindex="0" hidden><span id="s-plan-more"></span>
    <div class="pcard"><div class="csec"><h5 id="s-plan-card-h" data-t="sidebar.plan"></h5><div class="plan" id="s-plan-all"></div></div></div>
  </div>
</div>
<div class="sec" id="s-skills-sec" hidden><h4 data-t="sidebar.skills"></h4><div class="plan" id="s-skills"></div></div>
<div class="sec" id="s-repos-sec" hidden><h4><span data-t="sidebar.repos"></span><span class="aside" id="s-repos-n"></span></h4>
  <div class="prow" id="p-repos" tabindex="0">
    <div id="s-repos"></div>
    <div class="pcard">
      <div class="chd"><div class="t" data-t="sidebar.reposTitle"></div><div class="sub" id="s-repos-sum"></div></div>
      <div id="s-repos-all"></div>
    </div>
  </div>
</div>
<div class="sec" id="s-journal-sec"><h4><span data-t="sidebar.journal"></span><span class="aside" data-t="sidebar.today"></span></h4>
  <div class="prow" id="p-journal" tabindex="0">
    <div class="jpeek" id="s-journal-peek" hidden></div>
    <div class="foot"><span id="s-journal" data-t="sidebar.nothingYet"></span><span id="s-journal-at"></span></div>
    <div class="pcard">
      <div class="chd"><div class="t" data-t="sidebar.journalTitle"></div><div class="sub" data-t="sidebar.journalSub"></div></div>
      <div class="csec" id="s-journal-all"></div>
    </div>
  </div>
</div>
<div class="sec" id="s-wait-sec" hidden><h4 data-t="sidebar.waiting"></h4><div id="s-waiting"></div></div>
<div class="sec" id="s-mon-sec" hidden><h4 data-t="sidebar.monitors"></h4><div id="s-monitors"></div></div>
<div class="sec" id="s-feed-sec" hidden><h4 data-t="sidebar.feeds"></h4><div id="s-feeds"></div></div>
<div class="sec"><h4 data-t="sidebar.usage"></h4>
  <div class="prow" id="p-usage" tabindex="0">
    <div class="tiles" id="s-tiles"></div>
    <div class="foot" id="s-foot"><span id="s-foot-l"></span><span id="s-foot-r"></span></div>
    <div class="foot" id="s-load" hidden data-t-title="sidebar.loadTitle"><span data-t="sidebar.load"></span><span id="s-load-v"></span></div>
    <div class="pcard">
      <div class="csec"><h5><span data-t="sidebar.context"></span><span data-t="sidebar.thisSession"></span></h5>
        <div class="big"><span class="p" id="s-tokens" data-t="sidebar.noTurn"></span><span class="of" id="s-of"></span><span class="c" id="s-cost">$0.00</span></div>
        <div class="bar" id="s-ctxbar"><i id="s-bar" style="width:0%"></i></div>
        <p class="cnote" id="s-ctxnote"></p>
        <p class="cnote" id="s-counters" hidden></p>
      </div>
      <div class="csec" id="s-quota-sec"><h5><span class="qlink" id="s-quota-head" data-t-title="sidebar.quotaTitle"><span data-t="sidebar.quota"></span></span><span id="s-quota-age"></span></h5><div id="s-quota"></div></div>
    </div>
  </div>
</div>
<div class="sec" id="s-host-sec" hidden><h4><span data-t="sidebar.host"></span><span class="aside" id="s-host-name"></span></h4>
  <div class="prow" id="p-host" tabindex="0">
    <div class="tiles" id="s-host-tiles"></div>
    <div class="pcard">
      <div class="csec"><h5><span id="s-host-head" data-t="sidebar.host"></span><span data-t="sidebar.everyFive"></span></h5><div id="s-host-rows"></div></div>
      <div class="csec">
        <div class="kv"><span data-t="sidebar.liveSessions"></span><span id="s-host-live"></span></div>
        <div class="kv"><span data-t="sidebar.avgContext"></span><span id="s-host-ctx"></span></div>
      </div>
    </div>
  </div>
</div>
<div class="side-acts live-only">
  <button class="act stop needs-net" id="stop-session" data-t-title="sidebar.sleepTitle"><span data-t="sidebar.sleep"></span></button>
  <button class="act" id="quiet-session" data-t-title="sidebar.quietTitle"><span data-t="sidebar.quiet"></span></button>
  <button class="act needs-net" id="autoclose-session" aria-pressed="false" data-t-title="sidebar.autocloseTitle"><span data-t="sidebar.autoclose"></span></button>
  <button class="act needs-net" id="autohandoff-session" aria-pressed="false" data-t-title="sidebar.autohandoffTitle"><span data-t="sidebar.autohandoff"></span></button>
  <button class="act needs-net" id="continue" data-t-title="sidebar.continueTitle"><span data-t="sidebar.continue"></span></button>
  <button class="act needs-net" id="restart" data-t-title="sidebar.restartTitle"><span data-t="sidebar.restart"></span></button>
  <button class="act needs-net" id="close" data-t-title="sidebar.archiveTitle"><span data-t="sidebar.archive"></span></button>
</div>
<div class="side-acts read-only"><button class="act reopen" id="reopen" data-t-title="sidebar.reopenTitle"><span data-t="sidebar.reopen"></span></button></div>
<p class="sub err-text" id="side-error"></p>
<div class="ver" id="ver-sec" hidden>
  <div class="prow" id="p-ver" tabindex="0"><span id="ver-line"></span>
    <div class="pcard"><div class="csec"><h5 id="ver-head">aegis</h5>
      <div class="kv"><span data-t="sidebar.running"></span><span id="ver-run"></span></div>
      <div class="kv dim" id="ver-ref-row" hidden><span data-t="sidebar.ref"></span><span class="path" id="ver-ref"></span></div>
      <div class="kv dim" id="ver-base-row" hidden><span data-t="sidebar.base"></span><span class="v" id="ver-base"></span></div>
      <div class="kv"><span data-t="sidebar.latest"></span><span id="ver-latest"></span></div>
    </div><div class="csec ver-upd" id="ver-upd" hidden></div></div>
  </div>
</div>`;

// A repo line's marks: a linked worktree, and nothing left to commit or push.
const RP_ICON = { worktree: "branch", clean: "read" };
const UPD_STEPS = ["installing", "checking", "restarting"];
const isTool = (m) => m.kind === "term" || m.kind === "browser";

function span(cls, text, name) {
  const el = document.createElement("span");
  el.className = cls;
  el.textContent = text;
  if (name) el.prepend(icon(name));
  return el;
}

// `actions` is what the page knows and the part must not: the session in front
// (`focused`), navigation (`go`), opening and closing sessions, the noted
// actions of the side (`sleep`, `quiet`, `autoclose`, `continue`, `restart`),
// `revealRow` for a monitor's tool call and `revealEntry` for a journal row,
// the page's socket call for a rename, and `setVersionTop` for the header's
// version line (the header owns that node, and this part owns what it says).
export class SidebarPart {
  constructor(mount, host, actions = {}) {
    this.mount = mount;
    this.t = mount.t || labelsFor("en");
    this.act = actions;
    this.root = mount.root || host;
    this.editing = new Set();
    this.session = null;

    const grip = document.createElement("div");
    grip.className = "grip";
    grip.id = "side-grip";
    grip.title = this.t("sidebar.gripTitle");
    this.el = document.createElement("aside");
    this.el.className = "side";
    this.el.id = "side";
    this.el.innerHTML = SIDEBAR_HTML;
    applyLabels(this.el, this.t);
    host.append(grip, this.el);
    this.grip = grip;

    const n = (id) => byId(this.el, id);
    this.title = n("s-title");
    this.handle = n("s-handle");
    this.at = n("s-at");
    this.worker = n("s-worker");
    this.status = n("s-status");
    this.cardTitle = n("c-title");
    this.cardSub = n("c-sub");
    this.harness = n("s-harness");
    this.model = n("s-model");
    this.effort = n("s-effort");
    this.perm = n("s-perm");
    this.cwd = n("s-cwd");
    this.server = n("s-server");
    this.exitRow = n("s-exit-row");
    this.exit = n("s-exit");
    this.ask = n("s-ask");
    this.planSec = n("s-plan-sec");
    this.plan = n("s-plan");
    this.planMore = n("p-plan");
    this.planMoreText = n("s-plan-more");
    this.planAll = n("s-plan-all");
    this.planH = n("s-plan-h");
    this.planCardH = n("s-plan-card-h");
    this.skillsSec = n("s-skills-sec");
    this.skills = n("s-skills");
    this.reposSec = n("s-repos-sec");
    this.reposN = n("s-repos-n");
    this.repos = n("s-repos");
    this.reposSum = n("s-repos-sum");
    this.reposAll = n("s-repos-all");
    this.journalSec = n("s-journal-sec");
    this.journal = n("s-journal");
    this.journalAt = n("s-journal-at");
    this.journalPeek = n("s-journal-peek");
    this.journalAll = n("s-journal-all");
    this.waitSec = n("s-wait-sec");
    this.waiting = n("s-waiting");
    this.monSec = n("s-mon-sec");
    this.monitors = n("s-monitors");
    this.feedSec = n("s-feed-sec");
    this.feeds = n("s-feeds");
    this.tiles = n("s-tiles");
    this.foot = n("s-foot");
    this.footL = n("s-foot-l");
    this.footR = n("s-foot-r");
    this.load = n("s-load");
    this.loadV = n("s-load-v");
    this.tokens = n("s-tokens");
    this.of = n("s-of");
    this.cost = n("s-cost");
    this.ctxbar = n("s-ctxbar");
    this.bar = n("s-bar");
    this.ctxnote = n("s-ctxnote");
    this.counters = n("s-counters");
    this.quotaHead = n("s-quota-head");
    this.quotaAge = n("s-quota-age");
    this.quota = n("s-quota");
    this.hostSec = n("s-host-sec");
    this.hostName = n("s-host-name");
    this.hostHead = n("s-host-head");
    this.hostTiles = n("s-host-tiles");
    this.hostRows = n("s-host-rows");
    this.hostLive = n("s-host-live");
    this.hostCtx = n("s-host-ctx");
    this.stop = n("stop-session");
    this.quietBtn = n("quiet-session");
    this.autoclose = n("autoclose-session");
    this.autohandoff = n("autohandoff-session");
    this.continueBtn = n("continue");
    this.restart = n("restart");
    this.closeBtn = n("close");
    this.reopen = n("reopen");
    this.error = n("side-error");
    this.verSec = n("ver-sec");
    this.verLine = n("ver-line");
    this.verHead = n("ver-head");
    this.verRun = n("ver-run");
    this.verRefRow = n("ver-ref-row");
    this.verRef = n("ver-ref");
    this.verBaseRow = n("ver-base-row");
    this.verBase = n("ver-base");
    this.verLatest = n("ver-latest");
    this.verUpd = n("ver-upd");

    this.side = new Side({ root: this.root, side: this.el, grip, onCardOpen: () => this.act.closeMonitorCard?.() || this.act.closeFeedCard?.() });
    this.icons();
    this.wire();

    // What the panel last drew, so a patch that changed nothing redraws nothing.
    this.quotaKey = null; // the provider the card's quota rows were drawn for
    this.tilesSig = null;
    this.journalFor = null;
    this.journalDay = "";
    this.journalTimer = null;
    this.reposFor = null;
    this.unsubRepos = null;
    this.verInfo = null;
    this.upd = { phase: "idle", lines: [], error: null, version: null };

    this.onUpdate = () => this.drawUpdate();
    this.unsubUpdate = this.mount.conn.subscribe(
      "update",
      (snap) => {
        this.upd = snap;
        this.drawUpdate();
      },
      (ops) => {
        for (const op of ops) if ("set" in op) this.upd = op.set;
        this.drawUpdate();
      },
    );
    this.versionTimer = setInterval(() => this.mount.conn.open && this.loadVersion(), 3600 * 1000);
  }

  icons() {
    this.stop.prepend(icon("sleep"));
    this.quietBtn.prepend(icon("quiet"));
    this.autoclose.prepend(icon("done"));
    this.autohandoff.prepend(icon("handoff"));
    this.continueBtn.prepend(icon("play"));
    this.restart.prepend(icon("restart"));
    this.closeBtn.prepend(icon("archive"));
    this.reopen.prepend(icon("reopen"));
  }

  wire() {
    this.quotaHead.addEventListener("click", () => this.act.go?.("#usage"));
    this.stop.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.sleep?.(s.key);
    });
    this.quietBtn.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.quiet?.(s.key, true);
    });
    this.autoclose.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.autoclose?.(s.key, !s.autoclose);
    });
    this.autohandoff.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.autohandoff?.(s.key, !s.autohandoff);
    });
    this.continueBtn.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.continueKey?.(s.key);
    });
    this.restart.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.restart?.(s.key);
    });
    this.closeBtn.addEventListener("click", () => {
      const s = this.act.focused?.();
      if (s) this.act.close?.(s);
    });
    this.reopen.addEventListener("click", () => this.act.reopen?.());
    this.editable(this.title, "title");
    this.editable(this.handle, "handle");
  }

  // -- the panel's own actions -----------------------------------------------
  // The desktop's collapse, or the phone's drawer: one button does both.
  toggle() {
    if (!this.act.drawer?.()) return this.side.toggleCollapsed();
    if (this.root.dataset.side === "open") this.closeDrawer();
    else this.root.dataset.side = "open";
  }

  closeDrawer() {
    if (this.root.dataset.side === "open") this.side.restState();
  }

  drawerOpen() {
    return this.root.dataset.side === "open";
  }

  closeCard() {
    return this.side.closeCard();
  }

  restState() {
    this.side.restState();
  }

  isEditing() {
    return this.editing.size > 0;
  }

  // The Archive and Quiet keys, on the session in front: the panel's own
  // buttons decide whether they can act.
  archive() {
    if (!this.closeBtn.disabled) this.closeBtn.click();
  }

  quietSession() {
    if (!this.quietBtn.disabled) this.quietBtn.click();
  }

  handleText() {
    return this.handle.textContent;
  }

  startRename(field) {
    byId(this.el, field === "title" ? "s-title" : "s-handle").click();
  }

  // The shown session's error, in the panel under its actions.
  fail(message) {
    this.error.textContent = message || "";
  }

  // -- what the page draws through the part ---------------------------------
  render(s) {
    if (!s) return;
    if (!this.editing.has("title")) this.title.textContent = s.title || this.t("sidebar.untitled");
    if (!this.editing.has("handle")) this.handle.textContent = s.handle;
    this.session = s;
    this.cardTitle.textContent = s.title || this.t("sidebar.untitled");
    const born = s.created_at ? this.t("sidebar.started", { time: new Date(s.created_at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) }) : "";
    this.cardSub.textContent = `${s.handle}${born}`;
    // A launch on a version outside the tested range shows it in the warning
    // colour (harness_versions.py, #456).
    const u = s.untested_version;
    this.harness.replaceChildren(factIcon("h", s.harness), `${s.harness_label || s.harness || ""}${u ? ` ${u.version || "?"}` : ""}`);
    this.harness.classList.toggle("warn", !!u);
    this.harness.title = u ? this.t("sidebar.untestedTitle", { state: this.t(`sidebar.untested.${u.state in { newer: 1, older: 1 } ? u.state : "unknown"}`), range: u.range }) : "";
    this.model.textContent = s.model_id && s.model_id !== s.model ? `${modelLine(s)} → ${s.model_id}` : modelLine(s);
    this.effort.replaceChildren(factIcon("e", s.effort), s.effort || "");
    this.perm.replaceChildren(factIcon("p", s.permission), s.permission || "");
    this.server.textContent = s.server || this.mount.conn.server || this.t("sidebar.thisServer");
    // The handle as peers address it, handle@server, and the badge a Fleet card
    // gives a queue worker.
    this.at.textContent = s.server || this.mount.conn.server ? `@${s.server || this.mount.conn.server}` : "";
    this.worker.hidden = !s.worker;
    this.worker.textContent = s.worker ? this.t("sidebar.worker", { queue: s.worker.queue }) : "";
    if (s.attention === undefined) {
      // The archived read view: a stored meta has no attention, so the state alone.
      this.status.textContent = s.state;
      this.status.className = "st";
    } else {
      this.status.replaceChildren(glyph(s.attention), document.createTextNode(` ${stateLabel(this.t, s.attention) || s.state}`));
      this.status.className = `st at-${s.attention}`;
    }
    this.ask.hidden = !s.attention_line;
    this.ask.textContent = s.attention_line || "";
    this.ask.className = `askbox at-${s.attention}`;
    this.drawPlan(s);
    this.drawSkills(s);
    this.drawJournal(s.key);
    this.followRepos(s.key);
    this.cwd.textContent = s.cwd;
    this.exitRow.hidden = !s.exit;
    this.exit.textContent = s.exit || "";
    const p = this.provider(s);
    if (`${s.server || ""}:${p ? p.name : ""}` !== this.quotaKey) this.drawSideQuota(s);
    this.drawUsage(s);
    this.drawWaiting(s);
    const mons = s.monitors || [];
    this.monSec.hidden = !mons.length;
    renderMonitors(this.monitors, mons, (id) => this.act.revealRow?.(id));
    const fds = s.feeds || [];
    this.feedSec.hidden = !fds.length;
    renderFeeds(this.feeds, fds, (op, params) => this.act.callFor?.(s.key, op, params));
    // A workflow's node is the engine's while its run lives, and a handed-off
    // session its successor's (#874).
    const held = !!this.act.locked?.(s) || !!s.continued_by;
    this.continueBtn.disabled = s.state === "working" || held;
    this.restart.disabled = held || !!s.old;
    this.stop.disabled = s.state === "asleep" || held;
    this.quietBtn.disabled = !["working", "waiting"].includes(s.attention) || held;
    this.autoclose.disabled = held;
    this.autoclose.setAttribute("aria-pressed", String(!!s.autoclose));
    // A queue worker's task owns it, so it never hands itself off.
    this.autohandoff.hidden = !!s.worker;
    this.autohandoff.disabled = held;
    this.autohandoff.setAttribute("aria-pressed", String(!!s.autohandoff));
    // The button shows no text (#1022), so its size goes in the tooltip.
    const at = s.autohandoff ? ` (${Math.round(s.autohandoff / 1000)}k)` : "";
    this.autohandoff.title = this.t("sidebar.autohandoffTitle") + at;
    this.closeBtn.disabled = held;
  }

  // The page reruns this when a quota reading lands or the view changes.
  drawQuota() {
    if (!this.session) return;
    this.drawSideQuota(this.session);
    this.tilesSig = null; // the countdowns and ticks moved
    this.drawUsage(this.session);
  }

  // The page reruns this when the shown session's server answers a host patch.
  drawHost() {
    const s = this.session;
    const server = s?.server || "";
    const h = s && this.hostOf(server);
    this.hostSec.hidden = !h;
    if (!h) return;
    const name = server || this.mount.conn.server || this.t("sidebar.thisServer");
    this.hostName.textContent = name;
    this.hostHead.textContent = this.t("sidebar.hostCard", { name });
    const meter = (label, pct) => tile(label, `${pct}%`, { pct, kind: "h", severity: hostSeverity(pct) });
    const tiles = [meter(this.t("gauges.cpu"), h.cpu), meter(this.t("gauges.ram"), h.ram.pct)];
    const rows = [hostRow(this.t("gauges.cpu"), h.cpu, ""), hostRow(this.t("gauges.ram"), h.ram.pct, `${h.ram.used_gb} / ${h.ram.total_gb} GB`)];
    if (h.disk) tiles.push(meter(this.t("gauges.disk"), h.disk.pct)); // the fullest
    rows.push(...diskRows(h, this.t));
    const net = h.net;
    if (net) {
      const sp = net.speed;
      tiles.push(tile(this.t("gauges.net"), net.ok ? `${net.rtt_ms} ms` : this.t("gauges.down"), { severity: net.ok ? "normal" : "critical" }));
      if (sp?.complete) tiles.push(tile(this.t("gauges.speed"), mbps(sp.bps), { small: this.t("gauges.mbps") }));
    }
    rows.push(...netRows(net, serverNow(), () => this.act.measureNet?.(server || null)));
    this.hostTiles.replaceChildren(...tiles);
    this.hostRows.replaceChildren(...rows);
    const live = (s.server ? this.on(s.server) : this.local()).filter((m) => m.state !== "asleep" && !isTool(m));
    const sized = live.filter((m) => m.context_window && m.context_tokens);
    this.hostLive.textContent = String(live.length);
    this.hostCtx.textContent = sized.length
      ? `${Math.round(sized.reduce((a, m) => a + Math.min(100, (100 * m.context_tokens) / m.context_window), 0) / sized.length)}%`
      : "–";
  }

  // The page's one-second tick: the plan's clocks move while the session shows.
  tickPlan(s) {
    if (s && (s.plan || []).length) this.tickSidePlan(s);
  }

  // The journal channel changed: the panel refetches unless it is away.
  journalChanged() {
    if (this.act.view?.() !== "session") {
      this.journalFor = null;
      return;
    }
    clearTimeout(this.journalTimer);
    this.journalTimer = setTimeout(() => this.drawJournal(this.journalFor, true), 300);
  }

  // -- plan, skills ----------------------------------------------------------
  // The plan in the panel: totals in the heading, each item's time, and the
  // doing spinner turning only while the agent works. The rows are rebuilt
  // only when the items change: a session publishes several times a second
  // during a turn, and a new row would restart the spinner's animation.
  drawPlan(s) {
    const plan = s.plan || [];
    this.planSec.hidden = !plan.length;
    this.plan.classList.toggle("live", s.attention === "working");
    const key = JSON.stringify([s.key, plan.map((i) => [i.text, i.state])]);
    if (this.plan.dataset.key !== key) {
      this.plan.dataset.key = key;
      this.drawPlanRows(plan);
    }
    this.tickSidePlan(s);
  }

  // The skills the session loaded, one row each in the order read, with how
  // many times skill_run ran its scripts.
  drawSkills(s) {
    const skills = s.skills || [];
    const runs = s.skill_runs || {};
    this.skillsSec.hidden = !skills.length;
    const key = JSON.stringify([skills, runs]);
    if (this.skills.dataset.key === key) return;
    this.skills.dataset.key = key;
    this.skills.replaceChildren(
      ...skills.map((n) => {
        const d = document.createElement("div");
        const r = runs[n] || 0;
        d.append(span("", ""), span("", n), span("t", r ? this.t(r > 1 ? "sidebar.run.other" : "sidebar.run.one", { n: r }) : ""));
        return d;
      }),
    );
  }

  // The panel keeps the two items done last and everything still to do; the
  // rest is one line whose card holds the whole plan. Each row carries its
  // index, so the clock finds its time cell in either list.
  drawPlanRows(plan) {
    const mark = { done: "done", doing: "working", pending: "waiting" };
    const row = (i, k) => {
      const d = document.createElement("div");
      d.className = i.state;
      d.dataset.k = k;
      d.append(glyph(mark[i.state] || "waiting"), span("", i.text), span("t", ""));
      return d;
    };
    const open = plan.findIndex((i) => i.state !== "done");
    const from = Math.max(0, (open < 0 ? plan.length : open) - 2);
    this.plan.replaceChildren(...plan.slice(from).map((i, k) => row(i, from + k)));
    this.planMore.hidden = !from;
    this.planMoreText.textContent = this.t("sidebar.moreDone", { n: from });
    this.planAll.replaceChildren(...(from ? plan.map(row) : []));
  }

  // The parts that move with the clock, updated in place every second: a
  // redraw would restart the spinner's animation each time.
  tickSidePlan(s) {
    const plan = s.plan || [];
    const t = planTimes(s);
    const done = plan.filter((i) => i.state === "done").length;
    let head = this.t("sidebar.planHead", { done, total: plan.length });
    if (t) head += this.t("sidebar.planWork", { work: dur(t.work), idle: dur(t.idle) });
    if (t && t.left != null) head += this.t("sidebar.planLeft", { left: dur(t.left) });
    this.planH.textContent = head;
    this.planH.title = head; // one line in the panel; whole here and in the card
    this.planCardH.textContent = head;
    for (const d of this.plan.querySelectorAll(":scope > div")) {
      const k = Number(d.dataset.k);
      const i = plan[k];
      d.lastChild.textContent = t && i && i.state !== "pending" ? dur(t.items[k]) : "";
    }
    for (const d of this.planAll.querySelectorAll(":scope > div")) {
      const k = Number(d.dataset.k);
      const i = plan[k];
      d.lastChild.textContent = t && i && i.state !== "pending" ? dur(t.items[k]) : "";
    }
  }

  // -- the journal -----------------------------------------------------------
  // The panel's Journal row: the shown session's two newest entries today and
  // its count; the card lists its five newest as the Journal view draws them.
  // Fetched when the shown session changes or the journal changes.
  noJournal() {
    this.journal.textContent = this.t("sidebar.nothingYet");
    this.journalAt.textContent = "";
    this.journalPeek.hidden = true;
    this.journalPeek.replaceChildren();
    this.journalAll.replaceChildren(span("cnote", this.t("sidebar.nothingJournaled")));
  }

  async drawJournal(key, force = false) {
    const day = new Date().toDateString(); // "today" moves at midnight
    if (!key || (!force && this.journalFor === key && this.journalDay === day)) return;
    // Another session's rows go at once: this one never shows them while it loads.
    if (this.journalFor !== key) {
      this.journal.textContent = "…";
      this.journalAt.textContent = "";
      this.journalPeek.hidden = true;
      this.journalPeek.replaceChildren();
      this.journalAll.replaceChildren();
    }
    this.journalFor = key;
    this.journalDay = day;
    const linked = key.includes("/");
    this.journalSec.hidden = linked;
    if (linked) return;
    let res;
    try {
      res = await this.mount.conn.call("journal.rows", { session: key, since: "today", limit: 5, counts: true });
    } catch {
      if (this.journalFor === key) {
        this.journalFor = null; // the next render retries
        this.noJournal();
      }
      return;
    }
    if (this.journalFor !== key) return;
    if (!res.rows.length) {
      this.noJournal();
      return;
    }
    const c = res.counts || {};
    const total = Object.values(c).reduce((a, b) => a + b, 0);
    this.journal.textContent =
      this.t(total === 1 ? "sidebar.entry.one" : "sidebar.entry.other", { n: total }) +
      (c.commit ? this.t(c.commit === 1 ? "sidebar.commit.one" : "sidebar.commit.other", { n: c.commit }) : "");
    this.journalAt.textContent = this.t("sidebar.last", { time: res.rows[0].time });
    this.journalPeek.replaceChildren(...res.rows.slice(0, 2).map((r) => {
      const line = document.createElement("div");
      line.className = `k-${r.kind}`;
      const g = span("g", "");
      g.append(entryIcon(r.glyph));
      line.append(g, span("x", (r.tag ? `${r.tag}: ` : "") + (r.hash ? `${r.hash} ` : "") + r.text), span("t", r.time));
      return line;
    }));
    this.journalPeek.hidden = false;
    const thread = document.createElement("div");
    thread.className = "jthread";
    thread.append(...res.rows.map((r) => {
      const row = journalEntry(r, false);
      row.classList.add("le");
      // On a phone the drawer covers the transcript: close it so the reveal shows.
      opens(row, () => {
        if (!r.source) return;
        this.act.revealEntry?.(r.source);
        this.side.closeCard();
        this.closeDrawer();
      });
      return row;
    }));
    this.journalAll.replaceChildren(thread);
  }

  // -- repos ----------------------------------------------------------------
  // Each repo or worktree the shown session wrote to, from the repos:<log_id>
  // channel (repos/service.py), which sends the whole list again only when it
  // changed. A session on a linked server has none here.
  followRepos(key) {
    if (this.reposFor === key) return;
    this.reposFor = key;
    if (this.unsubRepos) this.unsubRepos();
    this.unsubRepos = null;
    this.reposSec.hidden = true;
    if (!key || key.includes("/")) return;
    const draw = (d) => {
      if (this.reposFor === key) this.drawRepos(d);
    };
    this.unsubRepos = this.mount.conn.subscribe(`repos:${this.act.forKey?.(key)?.log_id}`, draw, (ops) => {
      for (const op of ops) if (op.set) draw(op.set);
    });
  }

  drawRepos(d) {
    const repos = d.repos || [];
    this.reposSec.hidden = !repos.length;
    if (!repos.length) return;
    const groups = repoGroups(repos);
    this.reposN.textContent = String(groups.length);
    const lines = groups.slice(0, 5).map((g) => repoLine(g, this.t));
    if (groups.length > 5) lines.push(span("rp-more", this.t("sidebar.more", { n: groups.length - 5 })));
    this.repos.replaceChildren(...lines);
    const sum = (k) => repos.reduce((a, r) => a + r[k], 0);
    const open = new Set(repos.flatMap((r) => r.prs.filter((p) => p.state === "open").map((p) => `${r.main}#${p.n}`))).size;
    this.reposSum.textContent = [
      rpPlural(this.t, groups.length, "sidebar.repo"),
      this.t("sidebar.dirty", { files: rpPlural(this.t, sum("dirty_n"), "sidebar.file") }),
      this.t("sidebar.unpushed", { commits: rpPlural(this.t, sum("unpushed_n"), "sidebar.commitN") }),
      this.t("sidebar.prsOpen", { n: open }),
    ].join(" · ");
    this.reposAll.replaceChildren(...groups.map((g) => repoSection(g, d.gh, this.t)));
  }

  // -- waiting ---------------------------------------------------------------
  // The sessions this one waits on (registry._waiting_for): a queue task's
  // worker or a spawned child, a row each; a click opens it, and a hover or a
  // long press shows its tab card (#667). A task still pending has no worker
  // yet, so its row names the queue and opens nothing.
  drawWaiting(s) {
    const list = s.waiting_for || [];
    this.waitSec.hidden = !list.length;
    this.waiting.replaceChildren(
      ...list.map((w) => {
        const key = w.key && this.act.keyOf?.(s.server, w.key);
        const row = document.createElement("div");
        row.className = `kv wait${key ? " go" : ""}`;
        const where = w.kind === "task" ? this.t("sidebar.queueTask", { queue: w.queue, status: w.status }) : w.status;
        row.append(span("", w.handle || this.t("sidebar.noWorker")), span("r", where));
        if (key) {
          row.dataset.id = key;
          row.tabIndex = 0;
          row.addEventListener("click", () => this.act.open?.(key));
          row.addEventListener("keydown", (ev) => ev.key === "Enter" && this.act.open?.(key));
        }
        return row;
      }),
    );
  }

  // -- usage, quota and host -------------------------------------------------
  quotaOf(server) {
    return (server ? this.mount.state.remote.get(server)?.quota : this.mount.state.quota) || { providers: [] };
  }

  hostOf(server) {
    return server ? this.mount.state.remote.get(server)?.host : this.mount.state.host;
  }

  provider(s) {
    return providerFor(s, this.quotaOf(s.server || "").providers || []);
  }

  local() {
    return this.mount.state.ordered.filter((m) => !m.server);
  }

  on(server) {
    return this.mount.state.ordered.filter((m) => m.server === server);
  }

  ctxPct(s) {
    if (s.context_tokens == null || !s.context_window) return null;
    return Math.min(100, Math.round((100 * s.context_tokens) / s.context_window));
  }

  fmtTokens(n) {
    return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
  }

  // The tiles and the card's context: on every patch of the session, rebuilt
  // only when what they show changed.
  // A session's load (load.py): cores over the last minute and RAM.
  loadText(l) {
    return this.t("sidebar.loadText", { cores: l.cpu.toFixed(1), ram: l.rss_gb.toFixed(1) });
  }

  drawUsage(s) {
    const now = serverNow();
    const p = this.provider(s);
    const windows = p && p.state !== "failed" ? p.windows : [];
    const pct = this.ctxPct(s);
    const tokens = s.context_tokens != null ? this.fmtTokens(s.context_tokens) : null;
    const cost = money(s.cost_usd);
    this.load.hidden = !s.load;
    if (s.load) this.loadV.textContent = this.loadText(s.load);

    this.tokens.textContent = tokens ?? this.t("sidebar.noTurn");
    this.of.textContent = tokens == null ? "" : s.context_window ? this.t("sidebar.ofContext", { window: this.fmtTokens(s.context_window), pct }) : this.t("sidebar.tokens");
    this.bar.style.width = `${pct || 0}%`;
    this.ctxbar.hidden = !s.context_window;
    this.ctxnote.textContent =
      tokens != null && !s.context_window ? this.t("sidebar.noWindow", { harness: s.harness_label }) : "";
    this.cost.textContent = cost;
    // What the cache did (cache.py): red when the warm calls miss.
    const cn = this.counters;
    cn.hidden = s.cache_hit == null;
    if (s.cache_hit != null) {
      const hit = document.createElement("span");
      hit.textContent = this.t("sidebar.cache", { pct: s.cache_hit });
      hit.classList.toggle("bad", !!s.cache_alarm);
      hit.title = s.cache_alarm ? this.t("sidebar.cacheAlarm") : "";
      cn.replaceChildren(this.t("sidebar.tools", { n: s.tool_calls ?? 0 }), hit, this.t("sidebar.compactions", { n: s.compactions ?? 0 }));
    }

    // With a share of the window, context leads and the quota follows; without
    // one, the quota's windows take the row and context moves to the foot.
    const lead = pct != null || !windows.length;
    const shown = windows.slice(0, lead ? 2 : 3);
    const left = !windows.length ? "" : lead ? cost : `${tokens ? `${tokens} ctx · ` : ""}${cost}`;
    const next = shown.find((w) => w.resets_at != null);
    const right = next ? countdown(next.resets_at - now) : "";
    const sig = JSON.stringify([
      pct,
      tokens,
      cost,
      p && p.state,
      shown.map((w) => [w.kind, Math.round(w.percent), w.severity, w.projected, Math.round(100 * (elapsed(w, now) || 0))]),
      left,
      right,
    ]);
    if (sig === this.tilesSig) return;
    this.tilesSig = sig;
    const tiles = [];
    if (lead) tiles.push(tile(this.t("gauges.context"), pct != null ? `${pct}%` : tokens ?? "–", { pct }));
    for (const w of shown) tiles.push(quotaTile(p, w, now));
    if (!windows.length) {
      tiles.push(tile(this.t("gauges.cost"), cost));
      tiles.push(tile(this.t("gauges.quota"), p ? this.t("gauges.error") : this.t("gauges.none"), { severity: p ? "stale" : "normal", title: p ? p.note : "" }));
    }
    this.tiles.replaceChildren(...tiles);
    this.footL.textContent = left;
    this.footR.replaceChildren(...(right ? [icon("again"), ` ${right}`] : []));
    this.foot.hidden = !left && !right;
  }

  // The card's quota rows: on a quota patch, the timer, a change of view, or a
  // session that now spends another provider; never on a plain sessions patch.
  drawSideQuota(s) {
    const p = this.provider(s);
    this.quotaKey = `${s.server || ""}:${p ? p.name : ""}`;
    const now = serverNow();
    this.quotaHead.textContent = p ? this.t("sidebar.quotaWith", { provider: p.label }) : this.t("sidebar.quota");
    if (!p) {
      this.quotaAge.textContent = "";
      const what = s.harness === "opencode" ? (s.model || "").split("/")[0] || this.t("sidebar.thisModel") : s.harness_label;
      const n = document.createElement("p");
      n.className = "cnote";
      n.textContent = this.t("sidebar.noQuota", { what });
      this.quota.replaceChildren(n);
      return;
    }
    this.quotaAge.textContent = p.read_at != null ? this.t("sidebar.read", { ago: ago(p.read_at) }) : ""; // "read just now", "read 2m ago"
    const rows = p.state === "failed" ? [] : p.windows.map((w) => quotaSideRow(p, w, now));
    if (p.state !== "ok") {
      const n = document.createElement("p");
      n.className = "cnote gnote";
      n.textContent = p.state === "stale" ? this.t("sidebar.stale", { note: p.note, age: age(now - p.read_at) }) : p.note;
      rows.push(n);
    }
    this.quota.replaceChildren(...rows);
  }

  // -- the version and update card ------------------------------------------
  async loadVersion() {
    let v;
    try {
      v = await this.mount.conn.call("server.version");
    } catch (e) {
      return;
    }
    const run = v.running;
    const dev = run.dev;
    const short = run.commit ? run.commit.slice(0, 7) : null;
    const shown = dev && short ? short : run.version || this.t("sidebar.versionUnknown");
    const behind = v.status === "behind";

    this.verHead.textContent = this.t("sidebar.aegisOn", { server: this.mount.conn.server });
    this.verRun.replaceChildren(span("v", shown), ...(dev ? [span("tag", "dev")] : []));
    // aegis-dev builds from the resolved commit, so the ref is often the commit again.
    const ref = run.ref && !(run.commit || "").startsWith(run.ref) ? run.ref : null;
    this.verRefRow.hidden = !ref;
    this.verRef.textContent = ref || "";
    this.verBaseRow.hidden = !dev || !run.version;
    this.verBase.textContent = run.version || "";
    const mark = v.status === "current" ? [span("ok", this.t("sidebar.current"), "read")] : behind ? [span("upd", this.t("sidebar.update"), "arrow-up")] : [];
    this.verLatest.replaceChildren(span("v", v.latest || this.t("sidebar.versionUnknown")), ...mark);
    this.verLine.replaceChildren(
      span("", "aegis"),
      span("v", shown),
      ...(dev ? [span("tag", "dev")] : behind ? [span("upd", this.t("sidebar.update"), "arrow-up")] : v.status === "current" ? [span("ok", "", "read")] : []),
    );
    this.verSec.hidden = false;

    this.verInfo = v;
    this.drawUpdate();
  }

  // The one line in the header's menu: the build, a dev tag or an update mark,
  // and the update button or its run while one is in flight. The header owns
  // the node; this part owns what it says.
  syncVersionTop() {
    const v = this.verInfo;
    if (!v) return;
    const run = v.running;
    const dev = run.dev;
    const short = run.commit ? run.commit.slice(0, 7) : null;
    const shown = dev && short ? short : run.version || "unknown";
    const behind = v.status === "behind";
    const nodes = [span("", shown), ...(dev ? [span("tag", "dev")] : behind ? [span("upd", "", "arrow-up")] : [])];
    if (behind && this.mayUpdate() && (this.upd.phase === "idle" || this.upd.phase === "failed")) {
      nodes.push(this.updButton(this.t("sidebar.update")));
    }
    if (UPD_STEPS.includes(this.upd.phase)) {
      const line = document.createElement("div");
      line.className = "upd-run";
      line.textContent = this.upd.phase === "restarting" ? this.t("sidebar.restarting") : this.t("sidebar.updating");
      nodes.push(line);
    }
    const how = v.install?.command;
    const ref = run.ref && !(run.commit || "").startsWith(run.ref) ? run.ref : null;
    const title = behind
      ? this.t(how ? "sidebar.versionBehindHow" : "sidebar.versionBehind", { version: run.version, latest: v.latest, how })
      : dev
        ? this.t("sidebar.versionDev", { commit: run.commit || this.t("sidebar.sourceTree"), ref: ref ? ` (${ref})` : "", version: run.version, latest: v.latest || this.t("sidebar.versionUnknown") })
        : this.t("sidebar.versionCurrent", { version: run.version, latest: v.latest || this.t("sidebar.versionUnknown") });
    this.act.setVersionTop?.(nodes, title);
  }

  updRunning() {
    return UPD_STEPS.includes(this.upd.phase);
  }

  mayUpdate() {
    return this.verInfo?.status === "behind" && this.verInfo.install?.can;
  }

  updButton(label) {
    const b = document.createElement("button");
    b.className = "upd-btn";
    b.textContent = label;
    b.addEventListener("click", (ev) => {
      ev.stopPropagation();
      this.startUpdate();
    });
    return b;
  }

  async startUpdate() {
    const n = [...this.mount.state.sessions.values()].filter((m) => m.server === null && m.state === "working").length;
    const busy = n ? this.t(n > 1 ? "sidebar.updateBusy.other" : "sidebar.updateBusy.one", { n }) : "";
    if (!(await ask(this.t("sidebar.updateQuestion", { latest: this.verInfo.latest, busy }), { ok: this.t("sidebar.updateOk"), root: this.root }))) return;
    try {
      await this.mount.conn.call("server.update");
    } catch (e) {
      this.upd = { ...this.upd, phase: "failed", error: e.message };
      this.drawUpdate();
    }
  }

  drawUpdate() {
    this.syncVersionTop();
    const sec = this.verUpd;
    const behind = this.verInfo?.status === "behind";
    sec.hidden = !behind && this.upd.phase === "idle";
    if (sec.hidden) return;
    const inst = this.verInfo?.install;
    const rows = [];
    const kv = (k, v) => {
      const d = document.createElement("div");
      d.className = "kv";
      d.append(span("", k), span("", v));
      return d;
    };
    const line = (cls, text) => {
      const d = document.createElement("div");
      d.className = cls;
      d.textContent = text;
      return d;
    };
    if (inst) rows.push(kv(this.t("sidebar.installedBy"), inst.by));
    if (this.upd.phase === "idle") {
      if (this.mayUpdate()) {
        rows.push(line("how", inst.command));
        const acts = line("acts", "");
        acts.append(this.updButton(this.t("sidebar.update")));
        rows.push(acts);
      } else if (inst?.can) {
        rows.push(line("how", this.t("sidebar.updateAdmin")), line("how", inst.command));
      } else {
        rows.push(line("how", inst?.command ? this.t("sidebar.updateShell", { command: inst.command }) : this.t("sidebar.pullReinstall")));
      }
    } else {
      const ul = document.createElement("ul");
      ul.className = "upd-steps";
      const at = this.upd.phase === "failed" ? -1 : UPD_STEPS.indexOf(this.upd.phase);
      const failedAt = UPD_STEPS.indexOf(this.upd.failed_at);
      UPD_STEPS.forEach((step, i) => {
        const li = document.createElement("li");
        li.textContent = this.t(`sidebar.step.${step}`);
        if (at >= 0) li.className = i < at ? "done" : i === at ? "doing" : "";
        else li.className = i < failedAt ? "done" : i === failedAt ? "bad" : "";
        ul.append(li);
      });
      rows.push(ul);
      if (this.upd.lines.length) {
        const pre = document.createElement("pre");
        pre.className = "upd-log";
        pre.textContent = this.upd.lines.slice(-8).join("\n");
        rows.push(pre);
      }
      if (this.upd.phase === "failed") {
        rows.push(line("err-text", this.t("sidebar.updateFailed", { error: this.upd.error, version: this.verInfo?.running.version || "" })));
        if (this.mayUpdate()) {
          const acts = line("acts", "");
          acts.append(this.updButton(this.t("sidebar.tryAgain")));
          rows.push(acts);
        }
      }
    }
    sec.replaceChildren(...rows);
  }

  // -- rename in place -------------------------------------------------------
  editable(node, field) {
    node.addEventListener("click", () => {
      const r = this.act.route?.();
      if (this.editing.has(field) || !r || (r.view !== "session" && r.view !== "read")) return;
      this.editing.add(field);
      const old = node.textContent;
      const box = document.createElement("input");
      box.className = "rename";
      box.value = field === "title" && old === this.t("sidebar.untitled") ? "" : old;
      node.replaceChildren(box);
      box.focus();
      box.select();
      const finish = async (save) => {
        if (!this.editing.has(field)) return;
        this.editing.delete(field);
        const value = box.value.trim();
        node.textContent = old;
        if (!save || !value || value === old) return;
        try {
          await this.act.callFor?.(r.id, "session.rename", { [field]: value });
          node.textContent = value;
          this.act.archiveInvalidate?.();
          this.fail("");
        } catch (e) {
          this.fail(e.message);
        }
      };
      box.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") finish(true);
        if (ev.key === "Escape") {
          ev.stopPropagation();
          finish(false);
        }
      });
      box.addEventListener("blur", () => finish(true));
    });
  }

  destroy() {
    clearInterval(this.versionTimer);
    clearTimeout(this.journalTimer);
    this.unsubUpdate?.();
    this.unsubRepos?.();
    this.side.destroy();
    this.grip.remove();
    this.el.remove();
  }
}

// -- the repos card's rows ----------------------------------------------------
const rpPlural = (t, n, one) => t(n === 1 ? `${one}.one` : `${one}.other`, { n });
const rpBase = (p) => p.split("/").filter(Boolean).pop() || p;

function prLink(pr) {
  if (!pr.url) return span("rp-pr", `#${pr.n}`);
  const a = document.createElement("a");
  a.className = "rp-pr";
  a.href = pr.url;
  a.target = "_blank";
  a.rel = "noopener";
  a.textContent = `#${pr.n}`;
  a.title = pr.title || "";
  return a;
}

// The lines of one repo, its main checkout and worktrees, form one group: the
// main checkout first, then the worktrees in the server's order (newest
// first). The server puts the same peers on every line of a repo, one entry
// per checkout they wrote in; a group names each peer once.
function repoGroups(repos) {
  const by = new Map();
  for (const r of repos) {
    if (!by.has(r.main)) by.set(r.main, []);
    by.get(r.main).push(r);
  }
  return [...by.values()].map((lines) => {
    lines.sort((a, b) => a.worktree - b.worktree);
    const peers = new Map();
    for (const r of lines) for (const p of r.peers) {
      if (!peers.has(p.handle)) peers.set(p.handle, []);
      if (!peers.get(p.handle).includes(p.where)) peers.get(p.handle).push(p.where);
    }
    return { name: lines[0].name, main: lines[0].main, lines, peers };
  });
}

function repoLine(g, t) {
  const line = document.createElement("div");
  line.className = "rp-line";
  if (g.peers.size) {
    const dot = span("rp-dot", "");
    dot.title = [...g.peers.keys()].map((h) => t("sidebar.alsoHere", { handle: h })).join("\n");
    line.append(dot);
  }
  const [r] = g.lines;
  const where = g.lines.length > 1 ? t("sidebar.checkouts", { n: g.lines.length }) : r.worktree ? rpBase(r.top) : r.branch || t("sidebar.detached");
  line.append(span("n", g.name), span("rp-br", where, g.lines.length === 1 && r.worktree ? RP_ICON.worktree : ""));
  const right = span("rp-r", "");
  const sum = (k) => g.lines.reduce((a, l) => a + l[k], 0);
  if (g.lines.every((l) => l.removed)) right.append(span("rp-gone", t("sidebar.removedShort")));
  for (const op of new Set(g.lines.map((l) => l.op).filter(Boolean))) right.append(span("rp-op", op));
  if (sum("dirty_n")) right.append(span("rp-dirty", `~${sum("dirty_n")}`));
  if (sum("unpushed_n")) right.append(span("rp-ahead", `↑${sum("unpushed_n")}`));
  const seen = new Set();
  for (const l of g.lines) for (const pr of l.prs) {
    if (pr.state !== "open" || seen.has(pr.n)) continue;
    seen.add(pr.n);
    right.append(prLink(pr));
  }
  if (g.lines.every((l) => l.clean)) right.prepend(span("rp-clean", "", RP_ICON.clean));
  line.append(right);
  return line;
}

function rpKv(k, v, cls = "") {
  const d = document.createElement("div");
  d.className = "kv";
  const key = span(cls, k);
  const val = typeof v === "string" ? span("", v) : v;
  d.append(key, val);
  return d;
}

function rpList(items, shown, total, t) {
  const ul = document.createElement("ul");
  ul.className = "rp-files";
  for (const [text, x] of items.slice(0, shown)) {
    const li = document.createElement("li");
    li.append(span("", text));
    if (x) li.append(span("x", x));
    ul.append(li);
  }
  if (total > shown) {
    const li = document.createElement("li");
    li.className = "more";
    li.textContent = t("sidebar.more", { n: total - shown });
    ul.append(li);
  }
  return ul;
}

// One checkout inside its repo's section: what this session left there.
function checkoutBlock(r, ghOk, t) {
  const sec = document.createElement("div");
  sec.className = "rp-co";
  const h = document.createElement("h6");
  h.append(span("", r.worktree ? t("sidebar.worktree", { name: rpBase(r.top) }) : t("sidebar.mainCheckout")));
  if (r.worktree) h.append(span("p", r.path));
  sec.append(h);
  if (r.removed) sec.append(rpKv(t("sidebar.state"), t("sidebar.removed")));
  else if (r.branch || r.upstream) sec.append(rpKv(t("sidebar.branch"), r.upstream ? `${r.branch} → ${r.upstream}` : t("sidebar.noUpstream", { branch: r.branch || t("sidebar.detached") })));
  if (r.stale) sec.append(rpKv(t("sidebar.state"), t("sidebar.notAnswered")));
  if (r.op) sec.append(rpKv(t("sidebar.inProgress"), r.op, "rp-op"));
  if (r.dirty_n) {
    sec.append(rpKv(t("sidebar.uncommittedLabel"), rpPlural(t, r.dirty_n, "sidebar.file"), "rp-dirty"));
    sec.append(rpList(r.dirty.map((f) => [f.path, f.stat]), 10, r.dirty_n, t));
  }
  if (r.unpushed_n) {
    sec.append(rpKv(t("sidebar.unpushedLabel"), rpPlural(t, r.unpushed_n, "sidebar.commitN"), "rp-ahead"));
    sec.append(rpList(r.unpushed.map((s) => [s]), 5, r.unpushed_n, t));
  }
  if (r.clean) sec.append(rpKv(t("sidebar.state"), t("sidebar.cleanPushed"), "rp-clean"));
  if (r.left_n) {
    sec.append(rpKv(t("sidebar.leftHere"), rpPlural(t, r.left_n, "sidebar.commitN")));
    sec.append(rpList(r.left.map((c) => [`${c.hash} ${c.subject}`]), 5, r.left_n, t));
  }
  for (const pr of r.prs) {
    const v = span("", "");
    v.append(prLink(pr), document.createTextNode(` ${[pr.title, pr.state, pr.ci].filter(Boolean).join(" · ")}`));
    sec.append(rpKv(t("sidebar.pr"), v));
  }
  if (r.prs.length && !ghOk) sec.append(rpKv("", t("sidebar.prUnknown")));
  if (r.others_n) sec.append(rpKv(t("sidebar.othersDirt"), t("sidebar.othersDirtNote", { files: rpPlural(t, r.others_n, "sidebar.file") })));
  return sec;
}

function repoSection(g, ghOk, t) {
  const sec = document.createElement("div");
  sec.className = "csec";
  const h = document.createElement("h5");
  h.className = "rp-h";
  h.append(span("", g.name));
  const main = g.lines.find((l) => !l.worktree);
  h.append(span("p", main ? main.path : g.main));
  sec.append(h, ...g.lines.map((r) => checkoutBlock(r, ghOk, t)));
  for (const [handle, wheres] of g.peers) {
    const d = document.createElement("div");
    d.className = "rp-peer";
    d.append(...iconText("dot", t("sidebar.alsoHere", { handle })));
    sec.append(d, rpList(wheres.map((w) => [w]), wheres.length, wheres.length, t));
  }
  return sec;
}
