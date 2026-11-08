// The New session view: the form, the picks, the first-message box (with its
// attachments, mic and command menu), the hints under the chips, the doctor
// row, the terminal and browser entries, and the share or link that fills it.
//
// The part builds the markup index.html used to hold, finds every node under
// the element it built and never asks the page for an id. Everything it cannot
// know — where a view goes, how a session's first message is sent, how a command
// menu lists the agents of a server, how a page's dictation engine is held — the
// page hands it as `actions`, until the full layout owns them (b8).
//
// The router calls show(fill) for a #new? query and focus() when the view
// shows; a selection or a link calls openWith({server, agent, model, cwd,
// prompt, from}); the + button and the spawn key call open(). The hints and the
// form's own state are the part's: which server a session starts on, each
// server's roster and models, one tip at a time, and the suggests the server's
// help.state answered for this person.

import { Attachments } from "../attach.js";
import { CommandMenu } from "../commands.js";
import { byId, q } from "../dom.js";
import { gorgoneion, icon } from "../glyphs.js";
import { modelMeta } from "../models.js";
import { offline } from "../net.js";
import { toast } from "../toast.js";

// The markup this part owns. The ids stay as they are: they are the browser
// suite's hooks, and two mounts' copies are harmless because every lookup is
// scoped to the part's own element.
const SPAWN_HTML = `
<form class="spawn" id="spawn">
  <div class="mark" id="sp-mark"><span>ægis</span></div>
  <h3 class="sp-head">Agents</h3>
  <div class="cwdline"><pick-chip class="srvpick" id="sp-server" name="server" aria-label="Server" title="The server the session starts on" hidden></pick-chip><span class="srvsep" id="sp-sep" hidden>:</span><input class="cwd" id="sp-cwd" spellcheck="false" aria-label="Working directory" title="Working directory: click to change"></div>
  <div class="atts" id="sp-atts" hidden></div>
  <div class="box">
    <textarea id="sp-text" rows="3" aria-label="First message"
      placeholder="What should the agent do? Enter starts the session and sends this; Shift+Enter adds a line."></textarea>
    <div class="acts">
      <button class="mic clip needs-net" id="sp-attach" type="button" title="Attach files" aria-label="Attach files"><svg class="ic i-clip" aria-hidden="true"><use href="#g-clip"/></svg></button><input type="file" id="sp-attach-pick" multiple hidden>
      <button class="mic needs-net" id="sp-mic" type="button" data-state="idle" title="Dictate (Alt+M)" aria-label="Dictate"><svg class="ic i-mic" aria-hidden="true"><use href="#g-mic"/></svg></button>
      <button class="send needs-net" type="submit" id="sp-go" title="Start the session" aria-label="Start the session"></button>
    </div>
  </div>
  <div class="cmd-menu" id="sp-cmd-menu" hidden><input id="sp-cmd-filter" hidden spellcheck="false" placeholder="/command"><div id="sp-cmd-rows"></div></div>
  <div class="chips">
    <pick-chip class="pick agent" id="sp-agent" name="agent" aria-label="Agent"></pick-chip>
    <pick-chip class="pick" id="sp-harness" name="harness" aria-label="Harness"></pick-chip>
    <pick-chip class="pick" id="sp-model" name="model" aria-label="Model" free></pick-chip>
    <pick-chip class="pick" id="sp-effort" name="effort" aria-label="Effort"></pick-chip>
    <pick-chip class="pick" id="sp-permission" name="permission" aria-label="Permission"></pick-chip>
    <button type="button" class="reset" id="sp-reset" hidden>reset</button>
  </div>
  <div class="hints" id="sp-hints">
    <div class="doc" id="hint-doctor" hidden>
      <div class="row"><span class="dot"></span><span class="sum" id="hint-doctor-sum"></span><button class="run" id="hint-doctor-go" type="button">/doctor</button><button class="more" id="hint-doctor-more" type="button" aria-expanded="false" title="What the doctor found" aria-label="What the doctor found"></button></div>
      <ul id="hint-doctor-list" hidden></ul>
    </div>
    <div class="hint" id="hint-tutorial" hidden><button class="go" id="hint-tutorial-go" type="button">Run <code>/tutorial</code> for a quick guide.</button><button class="x" id="hint-tutorial-x" type="button" title="Dismiss" aria-label="Dismiss"></button></div>
    <div class="hint" id="hint-whats-new" hidden><button class="go" id="hint-whats-new-go" type="button">Run <code>/whats-new</code> to learn what's new in <span id="hint-whats-new-v"></span>.</button><button class="x" id="hint-whats-new-x" type="button" title="Dismiss" aria-label="Dismiss"></button></div>
    <div class="hint" id="hint-feedback" hidden><button class="go" id="hint-feedback-go" type="button">Run <code>/feedback</code> to report a bug or ask for a feature.</button></div>
    <div class="hint tip" id="hint-tip" hidden><span class="lab"></span><button class="go" id="hint-tip-go" type="button"></button></div>
  </div>
  <p class="err-text" id="sp-error"></p>
  <section id="sp-terms" class="sp-terms" aria-label="Terminals">
    <h3>Terminals</h3>
    <div class="sp-term-list"></div>
  </section>
</form>`;

const LAST_AGENT = "aegis.lastAgent";
const PICKS = ["harness", "model", "effort", "permission"];

export class SpawnPart {
  // `actions`: go(hash), keyOf(server, id), untestedRetry(call, again) and the
  // cancelled value it resolves to, sendFirst(key, text, files, dictated),
  // sendLine(text, false, [], session), stackActs(textarea), pasted(event) and
  // agents(server) for the command menu's completions. `dictation` is the
  // page's DictationPart, which gives this box a second target and finishes
  // its take before a send.
  constructor(mount, host, actions = {}) {
    this.mount = mount;
    this.act = actions;
    this.roster = { agents: [], harnesses: [], models: {}, default: null, cwd: "" };
    // What each harness can run, by server: the catalog config.detect reads
    // from the harness itself, the one the server validates a model change
    // against. The agents' own models are on offer until it answers, and after.
    this.spModels = { server: undefined, by: {} };
    // The server a new session starts on: null for this one. Its directory
    // line names it once a server is linked, since a path exists on one
    // machine only.
    this.spServer = null;
    // What help.state said for this person when the new tab last opened; null
    // until it answers, and then no hint shows.
    this.helpState = null;
    this.tips = [];
    this.previews = new Map();
    // The roster load in flight, so a caller can wait for the one already
    // running (openWith right after a connect).
    this.agentsLoading = Promise.resolve();
    // The local session the form was opened from: the new session joins its
    // group (#586). The + tab and a spawn clear it.
    this.spawnFrom = null;
    // Draft keys (here, "spawn") that got dictated text since their last send.
    this.spoken = new Set();
    this.tipAt = 0;
    this.tipHeld = false;
    this.tipTimer = null;
    this.touch = matchMedia("(pointer: coarse)");

    this.el = document.createElement("main");
    this.el.className = "view v-spawn";
    this.el.innerHTML = SPAWN_HTML;
    host.insertBefore(this.el, host.querySelector(".v-settings") || null);

    const n = (id) => byId(this.el, id);
    this.form = n("spawn");
    this.serverPick = n("sp-server");
    this.sep = n("sp-sep");
    this.cwd = n("sp-cwd");
    this.attsTray = n("sp-atts");
    this.text = n("sp-text");
    this.attachBtn = n("sp-attach");
    this.attachPick = n("sp-attach-pick");
    this.mic = n("sp-mic");
    this.go = n("sp-go");
    this.menuBox = n("sp-cmd-menu");
    this.menuRows = n("sp-cmd-rows");
    this.menuFilter = n("sp-cmd-filter");
    this.agentPick = n("sp-agent");
    this.harness = n("sp-harness");
    this.model = n("sp-model");
    this.effort = n("sp-effort");
    this.permission = n("sp-permission");
    this.reset = n("sp-reset");
    this.hints = n("sp-hints");
    this.error = n("sp-error");
    this.terms = n("sp-terms");
    this.termList = q(this.el, ".sp-term-list");
    this.doctorRow = n("hint-doctor");
    this.doctorSum = n("hint-doctor-sum");
    this.doctorList = n("hint-doctor-list");
    this.doctorMore = n("hint-doctor-more");
    this.tutorial = n("hint-tutorial");
    this.whatsNew = n("hint-whats-new");
    this.whatsNewV = n("hint-whats-new-v");
    this.feedback = n("hint-feedback");
    this.tipRow = n("hint-tip");
    this.tipGo = n("hint-tip-go");

    // The part draws its own glyphs; the sprite is in the mount's root.
    this.go.append(icon("send"));
    n("hint-tutorial-x").append(icon("close"));
    n("hint-whats-new-x").append(icon("close"));
    this.doctorMore.append(icon("caret-r"));
    this.tipRow.querySelector(".lab").append(icon("sparkle"));
    n("sp-mark").prepend(gorgoneion());

    // The new tab's files wait as chips until its session exists (submit).
    this.atts = new Attachments(this.attsTray, {
      call: (key, op, params) => this.mount.callFor(key, op, params),
      waitOnline: () => this.waitOnline(),
      hold: true,
    });
    this.atts.show("spawn");

    this.menu = new CommandMenu({
      box: this.menuBox,
      rows: this.menuRows,
      filter: this.menuFilter,
      load: () => this.loadPreview(),
      run: () => this.submit(),
      getLine: () => this.text.value,
      setLine: (v) => {
        this.text.value = v;
        this.grow();
        this.text.focus();
      },
      meta: () => ({ model: this.model.value }),
    });

    this.wire();
    this.drawServerPick();
    if (this.touch.matches) this.text.placeholder = "What should the agent do?";

    // The first message grows with its text until the form fills the view, and
    // only then scrolls (#384). The room left changes with the window and with
    // what sits around the box, so those are watched too.
    this.room = new ResizeObserver(() => this.grow());
    for (const node of [this.el, this.attsTray, this.hints, this.error]) this.room.observe(node);

    // The page's one dictation engine, with this box as another target. A take
    // that ends after its tab was left waits in the tray rather than going out
    // unseen (record.js, #932).
    this.act.dictation?.add(this.form, {
      input: () => this.text,
      mic: this.mic,
      heard: () => this.spoken.add("spawn"),
      ctx: () => ({ spawn: true }),
      onTake: (file) => {
        this.atts.add([file], "spawn");
        if (this.active()) this.submit();
      },
    });
  }

  // The router: a #new? query, read once by render and dropped from the URL.
  async show(fill) {
    if (!fill) return;
    const shared = fill.get("share") && self.caches ? await this.takeShare(fill.get("share")) : { text: "", files: [] };
    const prompt = [fill.get("prompt") || "", shared.text].filter(Boolean).join("\n\n");
    const t = this.text;
    t.value = prompt + (prompt && t.value ? "\n\n" : "") + t.value;
    this.grow();
    if (shared.files.length) this.atts.add(shared.files, "spawn");
    const server = fill.get("server") || "";
    if (server && server !== this.mount.conn.server) {
      await this.mount.state.linksReady;
      if (this.mount.linkOf(server)?.state !== "linked")
        return toast(`${server} is not a linked server: the session would start on this one`, {
          kind: "error",
          root: this.mount.root,
        });
      await this.setSpServer(server);
    }
    await this.agentsLoading;
    if (fill.get("cwd")) this.cwd.value = fill.get("cwd");
  }

  focus() {
    this.text.focus();
  }

  // The + button and the spawn key: a fresh form on this server.
  open() {
    this.spawnFrom = null;
    if (!offline()) this.act.go("#new");
  }

  // A dictation key while this view shows: the first-message box's take.
  dictate() {
    if (offline()) return;
    if (!navigator.mediaDevices) return;
    this.act.dictation?.toggle(this.text, "key");
  }

  // A source session filled the form: its server, agent, model and folder, and
  // a prompt the person goes on writing. The picks come from the source's
  // server's roster once it has loaded; pickAgent resets the chips to that
  // agent, so nothing a person set in the form before carries over. What the
  // form's text held stays after the prompt.
  async openWith({ server = null, agent, model, cwd, prompt, from = null }) {
    this.spawnFrom = from;
    this.text.value = prompt + this.text.value;
    this.act.go("#new");
    this.grow();
    this.agentPick.dataset.picked = agent;
    await this.setSpServer(server || null);
    await this.agentsLoading; // the boot's or a reconnect's load may still be out
    if (this.roster.agents.some((a) => a.name === agent)) this.pickAgent(agent);
    if (model && model !== "default") {
      this.model.value = model;
      this.markDiffs();
    }
    this.cwd.value = cwd || "";
  }

  // A directory picked elsewhere (a Files row's menu): this server, that
  // folder, and nothing sent.
  async openAt(dir) {
    this.spawnFrom = null;
    this.act.go("#new");
    this.grow();
    await this.setSpServer(null);
    await this.agentsLoading; // roster.cwd comes with the roster
    this.cwd.value = dir ? `${this.roster.cwd}/${dir}` : this.roster.cwd;
  }

  // Text for the first message when there is no session to write it into.
  prefill(text) {
    this.spawnFrom = null;
    this.text.value = `${text}\n\n${this.text.value}`;
    this.act.go("#new");
    this.grow();
  }

  // The roster and the server's own model catalog, as the page's connect and
  // config changes ask for them.
  loadAgents() {
    this.agentsLoading = this.fetchAgents();
    return this.agentsLoading;
  }

  // The links changed: the server chip names the ones a session may start on.
  drawServerPick() {
    const pick = this.serverPick;
    const show = this.mount.state.links.length > 0;
    pick.hidden = this.sep.hidden = !show;
    if (!show) {
      if (this.spServer !== null) this.setSpServer(null);
      return;
    }
    pick.options = [
      { value: "", label: this.mount.conn.server || "this server" },
      ...this.mount.state.links.map((l) => ({
        value: l.name,
        label: l.state === "linked" ? l.name : `${l.name} (${l.state})`,
        disabled: l.state !== "linked",
      })),
    ];
    if (this.spServer && this.mount.linkOf(this.spServer)?.state !== "linked") this.setSpServer(null);
    pick.value = this.spServer || "";
  }

  // Resolves once the picked server's roster has loaded.
  setSpServer(server) {
    if (server === this.spServer) return Promise.resolve();
    this.spServer = server;
    this.serverPick.value = server || "";
    this.cwd.value = ""; // the other machine's directories
    delete this.agentPick.dataset.picked;
    const loaded = this.loadAgents();
    this.drawHints();
    return loaded;
  }

  // The form's Terminals group: one button per profile on the picked server,
  // the built-in shell first.
  drawProfiles(list, server) {
    this.terms.hidden = !list.length;
    this.termList.replaceChildren(
      ...list.map((p) => {
        const b = document.createElement("button");
        b.type = "button";
        b.dataset.profile = p.name;
        b.disabled = Boolean(p.error);
        b.title = p.error || p.cmd || "your login shell";
        const name = document.createElement("b");
        name.textContent = p.name;
        const what = document.createElement("span");
        what.textContent = p.error || p.cmd || "login shell";
        b.append(name, " ", what);
        b.addEventListener("click", () =>
          this.mount.call("term.spawn", { profile: p.name }, server).then(
            (card) => this.act.go(`#t=${this.mount.keyOf(server, card.id)}`),
            (e) => toast(e.message, { kind: "error", root: this.mount.root }),
          ),
        );
        return b;
      }),
    );
  }

  // A browser under the terminals: a page on the server, held by the person.
  drawBrowserEntry(server) {
    const b = document.createElement("button");
    b.type = "button";
    b.dataset.browser = "";
    b.title = "a browser on the server you and an agent can share";
    const name = document.createElement("b");
    name.textContent = "browser";
    const what = document.createElement("span");
    what.textContent = "a page on the server";
    b.append(name, " ", what);
    b.addEventListener("click", () =>
      this.mount.call("browser.spawn", { url: "about:blank" }, server).then(
        (card) => this.act.go(`#b=${this.mount.keyOf(server, card.id)}`),
        (e) => toast(e.message, { kind: "error", root: this.mount.root }),
      ),
    );
    this.terms.hidden = false;
    this.termList.querySelector("[data-browser]")?.remove();
    this.termList.append(b);
  }

  async fetchAgents() {
    this.mount.call("term.profiles", {}, this.spServer).then(
      (list) => this.drawProfiles(list, this.spServer),
      () => this.drawProfiles([], this.spServer), // a server without terminals
    ).then(() => this.mount.call("browser.available", {}, this.spServer)).then(
      (a) => a.ok && this.drawBrowserEntry(this.spServer),
      () => {}, // a server from before browsers
    );
    // The picked agent as the chips last knew it, to tell the person's
    // overrides from values an edit to .aegis.yaml has since changed.
    const was = this.current();
    try {
      this.roster = await this.mount.call("agents.list", {}, this.spServer);
    } catch (e) {
      toast(e.message, { kind: "error", root: this.mount.root });
      return;
    }
    // A reconnect or a config change rebuilds the options; the chips keep what
    // the person set and follow the agent everywhere else.
    const before = Object.fromEntries(PICKS.map((k) => [k, this[k].value]));
    this.harness.options = this.roster.harnesses.map((h) => ({
      value: h.name,
      label: h.supported ? h.name : `${h.name} (not supported yet)`,
      disabled: !h.supported,
    }));
    this.agentPick.options = this.roster.agents.map((a) => {
      const why = a.error || (a.enabled ? "" : `${a.harness} is not supported yet`);
      return { value: a.name, label: why ? `${a.name} (${why})` : a.name, disabled: !a.enabled };
    });
    const usable = this.roster.agents.filter((a) => a.enabled).map((a) => a.name);
    const keep = this.agentPick.dataset.picked;
    const start = [keep, localStorage.getItem(LAST_AGENT), this.roster.default].find((n) => usable.includes(n)) || usable[0];
    if (!this.cwd.value) this.cwd.value = this.roster.cwd;
    this.error.textContent = this.roster.config_error
      ? `.aegis.yaml does not parse; aegis is using the last version that did. ${this.roster.config_error}`
      : this.roster.agents.length
        ? ""
        : "No agents yet. Set them up in Settings (Alt+S).";
    if (keep && usable.includes(keep)) {
      this.agentPick.value = keep;
      const now = this.current();
      for (const k of PICKS) this[k].value = was && before[k] === was[k] ? now[k] : before[k];
      this.fillModels(this.harness.value);
      this.markDiffs();
    } else if (start) this.pickAgent(start);
    this.loadSpModels();
  }

  current() {
    return this.roster.agents.find((a) => a.name === this.agentPick.value);
  }

  async loadSpModels() {
    const server = this.spServer;
    let found;
    try {
      found = await this.mount.call("config.detect", {}, server);
    } catch {
      return; // the agents' models stay on offer, and any text is still taken
    }
    if (server !== this.spServer) return;
    this.spModels = { server, by: Object.fromEntries(found.map((f) => [f.harness, f.models])) };
    this.fillModels(this.harness.value);
  }

  fillModels(harness) {
    const listed = this.spModels.server === this.spServer ? this.spModels.by[harness] || [] : [];
    const have = new Set(listed.map((m) => m.value));
    const named = (this.roster.models[harness] || []).filter((m) => !have.has(m));
    this.model.options = [
      ...listed.map((m) => ({ value: m.value, label: m.value, meta: modelMeta(m) })),
      ...named,
    ];
  }

  pickAgent(name) {
    const a = this.roster.agents.find((x) => x.name === name);
    if (!a) return;
    this.agentPick.value = a.name;
    this.agentPick.dataset.picked = a.name;
    this.fillModels(a.harness);
    for (const k of PICKS) this[k].value = a[k];
    this.markDiffs();
  }

  overrides() {
    const a = this.current();
    const out = {};
    if (!a) return out;
    for (const k of PICKS) {
      const v = this[k].value.trim();
      if (v && v !== a[k]) out[k] = v;
    }
    return out;
  }

  markDiffs() {
    const a = this.current();
    const diff = this.overrides();
    for (const k of PICKS) this[k].classList.toggle("diff", k in diff);
    const changed = Object.keys(diff).length > 0;
    this.reset.hidden = !changed;
    this.agentPick.suffix = changed ? "*" : "";
  }

  async submit() {
    const a = this.current();
    if (!a || this.go.disabled || offline()) return;
    this.go.disabled = true;
    this.error.textContent = "";
    await this.act.dictation?.finish(this.text);
    const text = this.text.value.trim();
    const params = { agent: a.name, cwd: this.cwd.value.trim() || null, ...this.overrides() };
    // With files or dictated text, the session starts empty: the files upload
    // into its inbox and go out with the text as its first message (sendFirst),
    // which carries the dictated mark a spawn's prompt has no field for.
    const dictated = this.spoken.has("spawn") && !this.spServer; // a linked server may predate the field
    const held = this.atts.list("spawn").length > 0 || dictated;
    // A / line runs as a command in the new session, as typed there (#570):
    // as spawn's prompt the harness would get it as plain text.
    const command = text.startsWith("/") && !text.startsWith("//");
    if (text && !held && !command) params.prompt = text;
    if (this.spawnFrom && !this.spServer) params.from_session = this.spawnFrom;
    try {
      const r = await this.act.untestedRetry(
        () => this.mount.call("session.spawn", params, this.spServer),
        (untested) => this.mount.call("session.spawn", { ...params, untested }, this.spServer),
      );
      if (r === this.act.cancelled) return; // the typed text stays
      this.spawnFrom = null;
      localStorage.setItem(LAST_AGENT, a.name);
      this.text.value = "";
      this.menu.close();
      this.grow();
      this.pickAgent(a.name);
      const key = this.mount.keyOf(this.spServer, r.log_id);
      this.act.go(`#s=${key}`);
      this.spoken.delete("spawn");
      if (held) this.act.sendFirst(key, text, this.atts.take("spawn"), dictated);
      else if (command) {
        // The session's patch lands before spawn's reply; a far one may not have.
        const s = this.mount.state.sessions.get(key);
        if (s) this.act.sendLine(text, false, [], s);
        else this.act.sendFirst(key, text, []);
      }
    } catch (e) {
      toast(e.message, { kind: "error", root: this.mount.root });
    } finally {
      this.go.disabled = false;
    }
  }

  // The first message grows with its text until the form fills the view, and
  // only then scrolls (#384): the new tab has nothing else to show. The room
  // left changes with the window and with what sits around the box (files,
  // hints, an error), so those are watched too. A hidden view has no height to
  // fill.
  grow() {
    // The tip and /feedback rows give way to a first message (client.css).
    this.hints.toggleAttribute("data-typing", this.text.value !== "");
    const t = this.text;
    const view = t.closest(".view");
    if (!view.clientHeight) return;
    this.act.stackActs?.(t);
    t.style.height = "";
    const css = getComputedStyle(view);
    const pad = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
    const room = view.clientHeight - pad - (this.form.offsetHeight - t.offsetHeight);
    t.style.height = `${Math.max(t.offsetHeight, Math.min(t.scrollHeight, room))}px`;
  }

  // The rows under the chips. /tutorial while this person has started no
  // tutorial, /whats-new while the release it runs is newer than their last
  // tour, /feedback always. For an admin, the doctor row while the server's
  // last doctor run found a problem (doctor_watch.py). Last, one hint from the
  // help pages at a time. The record is the server's, per principal
  // (helpstate.py), read each time the new tab opens, so every device shows the
  // same rows. The actions act on this server only: a linked server keeps its
  // own record and may not know help.guide. The tip stays, since the Help tab
  // is this server's.
  async loadHints() {
    delete this.hints.dataset.ready;
    try {
      this.helpState = await this.mount.call("help.state", {});
    } catch {
      this.helpState = null; // no hint rather than a wrong one
    }
    this.tips = this.shuffled(this.helpState?.hints || []);
    this.drawHints();
    this.rotateTip(true);
    this.hints.dataset.ready = "";
  }

  drawHints() {
    const away = !this.helpState || this.spServer !== null;
    this.tutorial.hidden = away || this.helpState.tutorial;
    this.whatsNew.hidden = away || !this.helpState.whats_new;
    this.whatsNewV.textContent = this.helpState?.whats_new || "";
    this.feedback.hidden = away;
    this.drawDoctor(away ? null : this.helpState.doctor);
  }

  // The doctor row: a count, /doctor, and the findings folded under the caret,
  // each named after what it is about rather than the doctor's dotted key.
  findingLabel(where) {
    const [head, name] = where.split(".");
    if (head === "agents" && name) return `Agent ${name}`;
    if (head === "queues" && name) return `Queue ${name}`;
    if (head === "harness" && name) return HARNESS_LABEL[name] || name;
    if (head === "service" && name) {
      // service.<unit>.<Directive>, and a unit's name has its own dot.
      const rest = where.slice(8);
      const cut = rest.search(/\.[A-Z][A-Za-z]*$/);
      return cut < 0 ? `Service ${rest}` : `Service ${rest.slice(0, cut)} · ${rest.slice(cut + 1)}`;
    }
    return { file: ".aegis.yaml", default_agent: "The default agent", state: "The state directory" }[where] || where;
  }

  drawDoctor(d) {
    const row = this.doctorRow;
    row.hidden = !d;
    if (!d) return;
    row.classList.toggle("err", d.errors > 0);
    row.classList.toggle("warn", d.errors === 0);
    const parts = [d.errors && counted(d.errors, "error"), d.warns && counted(d.warns, "warning")].filter(Boolean);
    this.doctorSum.replaceChildren("This server's setup has ", Object.assign(document.createElement("b"), { textContent: parts.join(" and ") }),
      `. Fix ${d.findings.length === 1 ? "it" : "them"} with`);
    const items = d.findings.map((f) => {
      const li = document.createElement("li");
      li.className = f.level;
      li.append(Object.assign(document.createElement("span"), { className: "w", textContent: this.findingLabel(f.where) }), f.message);
      return li;
    });
    const tail = Object.assign(document.createElement("li"), { className: "tail" });
    const a = Object.assign(document.createElement("a"), { href: "#settings", textContent: "Settings" });
    tail.append("Each one is also marked on its row in ", a, ".");
    this.doctorList.replaceChildren(...items, tail);
  }

  shuffled(list) {
    const out = list.slice();
    for (let i = out.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [out[i], out[j]] = [out[j], out[i]];
    }
    return out;
  }

  showTip(fade) {
    const go = this.tipGo;
    const put = () => {
      const tip = this.tips[this.tipAt % this.tips.length];
      go.textContent = tip.text;
      go.title = tip.text;
      go.dataset.ref = tip.ref;
      go.classList.remove("fade");
    };
    if (!fade || matchMedia("(prefers-reduced-motion: reduce)").matches) return put();
    go.classList.add("fade");
    setTimeout(put, 300);
  }

  rotateTip(fresh = false) {
    clearInterval(this.tipTimer);
    this.tipRow.hidden = !this.tips.length;
    if (!this.tips.length) return;
    if (fresh) this.tipAt = 0;
    this.showTip(false);
    this.tipTimer = setInterval(() => {
      if (this.tipHeld || document.hidden || this.tipRow.closest(".view")?.hidden) return;
      this.tipAt++;
      this.showTip(true);
    }, TIP_MS);
  }

  // What running or dismissing a hint records, as the server records it
  // (help_ops.help_guide, help_dismiss), so the hint hides without a new read.
  ranHint(kind) {
    if (!this.helpState) return;
    this.helpState = kind === "tutorial" ? { ...this.helpState, tutorial: true } : { ...this.helpState, whats_new: null };
  }

  // A guide session (help.guide), and its tab, as a spawn from the new tab
  // opens one. `params` are spawn fields; none at all means default_agent.
  // `marks`: whether the server records this as the tutorial taken (a tutorial
  // page) so the hint hides; a tour of another page or a question marks
  // nothing. The Help tab calls this for its rows.
  async startGuide(params, kind = "tutorial", marks = true) {
    const r = await this.act.untestedRetry(
      () => this.mount.call("help.guide", { kind, ...params }),
      (untested) => this.mount.call("help.guide", { kind, ...params, untested }),
    );
    if (r === this.act.cancelled) return null;
    if (marks) this.ranHint(kind);
    this.drawHints();
    this.act.go(`#s=${r.log_id}`);
    return r;
  }

  // A hint runs what Enter would, with the chips as they stand.
  async guideFromComposer(kind, btn) {
    const a = this.current();
    if (!a || btn.disabled) return;
    btn.disabled = true;
    this.error.textContent = "";
    try {
      const marks = kind === "tutorial" || kind === "whats_new";
      // /doctor runs in the config root unless the person typed a directory.
      await this.startGuide({ agent: a.name, cwd: this.cwd.value.trim() || null, ...this.overrides() }, kind, marks);
      localStorage.setItem(LAST_AGENT, a.name);
      this.pickAgent(a.name);
    } catch (e) {
      toast(e.message, { kind: "error", root: this.mount.root });
      if (kind === "doctor") this.loadHints(); // fixed meanwhile: the row goes
    } finally {
      btn.disabled = false;
    }
  }

  // The cross records what running the hint would, and starts nothing.
  async dismissHint(kind, row) {
    row.hidden = true;
    try {
      await this.mount.call("help.dismiss", { what: kind });
      this.ranHint(kind);
    } catch (e) {
      toast(e.message, { kind: "error", root: this.mount.root });
      this.drawHints();
    }
  }

  // The new tab's menu (#632): aegis's commands before the session exists, and
  // the harness's when a session in that directory has already listed them. A
  // whole command typed here runs in the new session (#570).
  async loadPreview() {
    const params = { harness: this.harness.value, cwd: this.cwd.value.trim() || null };
    const k = JSON.stringify([this.spServer, params]);
    if (!this.previews.has(k)) {
      this.previews.set(
        k,
        Promise.all([
          this.mount.call("commands.preview", params, this.spServer),
          this.mount.call("help.pages", {}, this.spServer).catch(() => []),
          this.act.agents(this.spServer),
        ]).then(([cat, pages, agents]) => ({ ...cat, agents, tutorials: pages.filter((pg) => pg.kind === "tutorial") })),
      );
    }
    try {
      return await this.previews.get(k);
    } catch {
      this.previews.delete(k); // a server without the op: no menu, as before
      return null;
    }
  }

  // What the service worker kept of a share (sw.js stash), taken out once.
  async takeShare(id) {
    const cache = await caches.open("aegis.share");
    const meta = await cache.match(`/share/${id}`);
    if (!meta) return { text: "", files: [] }; // taken already, or replaced by a newer share
    const { text, files } = await meta.json();
    const blobs = await Promise.all(files.map((_, i) => cache.match(`/share/${id}/${i}`).then((r) => r?.blob())));
    await caches.delete("aegis.share");
    return { text, files: files.flatMap((f, i) => (blobs[i] ? [new File([blobs[i]], f.name, { type: f.type })] : [])) };
  }

  // Whether this view is the one the page shows. Only the full layout has a
  // view state; a host's chat mounts no spawn form.
  active() {
    return this.mount.root?.dataset.view === "spawn";
  }

  waitOnline() {
    return new Promise((resolve) => {
      const t = setInterval(() => {
        if (this.mount.conn.open) {
          clearInterval(t);
          resolve();
        }
      }, 500);
    });
  }

  wire() {
    this.serverPick.addEventListener("change", () => this.setSpServer(this.serverPick.value || null));
    this.effort.options = ["low", "medium", "high", "xhigh", "max"].map((e) => ({ value: e, label: `effort ${e}` }));
    this.permission.options = ["read", "write", "auto", "full"].map((p) => ({ value: p, label: `perm ${p}` }));
    this.agentPick.addEventListener("change", () => this.pickAgent(this.agentPick.value));
    this.harness.addEventListener("change", () => {
      this.fillModels(this.harness.value);
      this.markDiffs();
    });
    for (const k of ["model", "effort", "permission"]) this[k].addEventListener("input", () => this.markDiffs());
    this.reset.addEventListener("click", () => this.pickAgent(this.agentPick.value));
    this.form.addEventListener("submit", (ev) => {
      ev.preventDefault();
      this.submit();
    });
    // Enter in a field would submit the form and spawn a half-written session;
    // there it means "done with this field". In an open chip it is the chip's pick.
    for (const node of [this.model, this.cwd]) {
      node.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter" && !ev.isComposing && !ev.defaultPrevented) {
          ev.preventDefault();
          this.text.focus();
        }
      });
    }
    // Enter spawns and sends; on a touch screen it adds a line, as in a session.
    this.text.addEventListener("input", async () => {
      const v = this.text.value;
      if (v.startsWith("/") && !v.startsWith("//")) {
        if (this.menu.isOpen) this.menu.refresh();
        else await this.menu.openInline();
      } else if (this.menu.isOpen) this.menu.close();
    });
    this.text.addEventListener("keydown", (ev) => {
      if (this.menu.onKey(ev)) return;
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !this.touch.matches) {
        ev.preventDefault();
        this.submit();
      }
    });
    this.text.addEventListener("input", () => this.grow());
    this.attachBtn.addEventListener("click", () => !offline() && this.attachPick.click());
    this.attachPick.addEventListener("change", (ev) => {
      this.atts.add([...ev.target.files], "spawn");
      ev.target.value = "";
    });
    this.text.addEventListener("paste", (ev) => {
      const files = this.act.pasted?.(ev);
      if (files) this.atts.add(files, "spawn");
    });

    this.doctorMore.addEventListener("click", () => {
      const open = this.doctorRow.classList.toggle("open");
      this.doctorList.hidden = !open;
      this.doctorMore.setAttribute("aria-expanded", String(open));
    });
    for (const [ev, held] of [["mouseenter", true], ["mouseleave", false], ["focusin", true], ["focusout", false]]) {
      this.tipRow.addEventListener(ev, () => (this.tipHeld = held));
    }
    this.tipGo.addEventListener("click", (e) => {
      const ref = e.currentTarget.dataset.ref;
      if (ref) this.act.go(`#help=${encodeURIComponent(ref)}`);
    });
    for (const [kind, id] of [["tutorial", "hint-tutorial"], ["whats_new", "hint-whats-new"]]) {
      const go = byId(this.el, `${id}-go`);
      const x = byId(this.el, `${id}-x`);
      go.addEventListener("click", () => this.guideFromComposer(kind, go));
      x.addEventListener("click", () => this.dismissHint(kind, byId(this.el, id)));
    }
    for (const [kind, id] of [["feedback", "hint-feedback-go"], ["doctor", "hint-doctor-go"]]) {
      const btn = byId(this.el, id);
      btn.addEventListener("click", () => this.guideFromComposer(kind, btn));
    }

    // A file dropped anywhere on the page while this view shows attaches here;
    // elsewhere the browser would navigate away to the file.
    this.onDragOver = (ev) => {
      if (!ev.dataTransfer?.types.includes("Files")) return;
      ev.preventDefault();
      this.form.classList.toggle("dropping", this.active());
    };
    this.onDragLeave = (ev) => {
      if (ev.relatedTarget) return;
      this.form.classList.remove("dropping");
    };
    this.onDrop = (ev) => {
      if (!ev.dataTransfer?.types.includes("Files")) return;
      ev.preventDefault();
      this.form.classList.remove("dropping");
      if (this.active()) this.atts.add([...ev.dataTransfer.files], "spawn");
    };
    document.addEventListener("dragover", this.onDragOver);
    document.addEventListener("dragleave", this.onDragLeave);
    document.addEventListener("drop", this.onDrop);
  }

  destroy() {
    clearInterval(this.tipTimer);
    this.room?.disconnect();
    document.removeEventListener("dragover", this.onDragOver);
    document.removeEventListener("dragleave", this.onDragLeave);
    document.removeEventListener("drop", this.onDrop);
    this.menu?.close?.();
    this.el.remove();
  }
}

const TIP_MS = 8000;
const HARNESS_LABEL = { "claude-code": "Claude Code", opencode: "OpenCode", codex: "Codex", cursor: "Cursor Agent" };
const counted = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
