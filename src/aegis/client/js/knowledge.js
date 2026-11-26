import { markdown } from "./entries.js";
import { open as openEditor } from "./editor.js";
import { ago } from "./fleet.js";
import { icon } from "./glyphs.js";
import { opens } from "./journal.js";
import { ask, cancelAsk, choose } from "./dialog.js";
import { rootOf } from "./root.js";

const KINDS = [
  ["all", "all"],
  ["skill", "skills"],
  ["memory", "memories"],
  ["flagged", "flagged"],
];

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined && text !== null) n.textContent = text;
  return n;
}

function button(cls, text, onClick) {
  const b = el("button", cls, text);
  b.type = "button";
  b.addEventListener("click", onClick);
  return b;
}

// The flag a row shows, and its colour class: a proposal first, then the
// server's flags (expired, stale:<path>), else ok.
function flagOf(r) {
  if (r.status === "proposed") return ["proposed", "prop", ""];
  const f = r.flags?.[0] || (r.expired ? "expired" : null);
  if (!f) return ["ok", "ok", ""];
  const [name, ...rest] = f.split(":");
  return [name, name === "expired" ? "exp" : "stale", rest.join(":")];
}

function used(r) {
  if (!r.last_used) return "never used";
  return `used ${ago(r.last_used)}`;
}

function when(ts) {
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export class Knowledge {
  constructor(conn, box, { onOpen, onBack }) {
    this.conn = conn;
    this.box = box;
    this.onOpen = onOpen;
    this.logId = "";
    this.pageContext = "";
    this.onBack = onBack;
    this.kind = "all";
    this.rows = [];
    this.name = null; // the item open in the detail pane
    this.ed = null;
    this.seq = 0;
    this.openSeq = 0;
    this.timer = null;
    this.drawn = false;
  }

  draw() {
    if (this.drawn) return;
    this.drawn = true;
    this.q = Object.assign(el("input", "jq kq"), { type: "search", spellcheck: false, autocomplete: "off" });
    this.q.placeholder = "Search knowledge";
    this.q.setAttribute("aria-label", "Search knowledge");
    this.q.setAttribute("autocapitalize", "off");
    this.q.addEventListener("input", () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => this.load(), 200);
    });
    const search = el("label", "jsearch");
    search.append(icon("search"), this.q);
    this.kinds = el("div", "jkinds kkinds");
    this.kinds.setAttribute("role", "group");
    this.kinds.setAttribute("aria-label", "Kind");
    for (const [k, label] of KINDS) {
      const b = button("jkind", label, () => {
        this.kind = k;
        this.load();
      });
      b.dataset.kind = k;
      this.kinds.append(b);
    }
    this.context = el("pick-chip", "kfilter");
    this.context.setAttribute("aria-label", "Session context");
    this.context.addEventListener("change", async () => {
      const next = this.context.value;
      if (!(await this.leave())) {
        this.context.value = this.logId;
        return;
      }
      this.logId = next;
      this.source.value = "";
      if (this.name) {
        this.closeItem();
        this.onBack();
      } else this.load();
    });
    this.source = el("pick-chip", "kfilter");
    this.source.setAttribute("aria-label", "Knowledge source");
    this.source.addEventListener("change", () => this.load());
    this.source.options = [{ value: "", label: "All sources" }];
    const filters = el("div", "kfilters");
    filters.append(this.context, this.source);
    this.count = el("div", "jcount kcount");
    this.count.setAttribute("aria-live", "polite");
    this.listErrors = el("div", "kerrors");
    this.list = el("div", "klist");
    this.detail = el("div", "kdetail");
    this.detail.hidden = true;
    const panes = el("div", "kpanes");
    panes.append(this.list, this.detail);
    const top = el("div", "ktop");
    this.synthesisButton = button("btn", "Synthesize a task", () => this.synthesize());
    this.synthesisButton.hidden = true;
    filters.append(this.synthesisButton);
    top.append(search, filters, this.kinds, this.count, this.listErrors);
    this.box.replaceChildren(top, panes);
  }

  options(select, choices, selected) {
    const existing = select.options;
    if (existing.length !== choices.length || choices.some(([value, label], i) => existing[i].value !== value || existing[i].label !== label)) {
      select.options = choices.map(([value, label]) => ({ value, label }));
    }
    select.value = selected;
  }

  contexts(sessions) {
    this.draw();
    this.sessions = sessions;
    const choices = [["", "No session context"], ...sessions.map((s) => [s.log_id, `${s.handle} · ${s.cwd}`])];
    if (this.logId && !choices.some(([id]) => id === this.logId)) choices.push([this.logId, "Unavailable session"]);
    this.options(this.context, choices, this.logId);
  }

  async show(name, context) {
    this.draw();
    const logId = context ?? this.logId;
    if ((name || null) !== this.name || (name && logId !== this.pageContext)) {
      if (!(await this.leave())) {
        if (this.name) this.onOpen(this.name, this.pageContext);
        return;
      }
      if (logId !== this.logId) this.source.value = "";
      this.logId = logId;
      this.contexts(this.sessions || []);
      if (name) this.openItem(name, logId);
      else this.closeItem();
    }
    this.box.dataset.open = name ? "1" : "";
    this.load();
    if (!name) this.q.focus();
  }

  navigate(id) {
    this.onOpen(id, this.logId);
  }

  // Whether the open edit may be dropped: asks when it has unsaved changes.
  async leave() {
    if (!this.ed?.dirty()) return true;
    return ask(`Discard your unsaved edit of ${this.name}?`, { ok: "Discard", cancel: "Keep editing" });
  }

  chips() {
    for (const b of this.kinds.children) b.setAttribute("aria-pressed", String(b.dataset.kind === this.kind));
  }

  async load() {
    clearTimeout(this.timer);
    const n = ++this.seq;
    this.chips();
    const query = this.q.value.trim();
    const p = { kind: this.kind, limit: 100 };
    if (query) p.query = query;
    if (this.logId) p.log_id = this.logId;
    if (this.source.value) p.source_id = this.source.value;
    let res;
    try {
      res = await this.conn.call("knowledge.catalog", p);
    } catch (e) {
      if (n !== this.seq) return;
      this.rows = [];
      this.render(p, e.message);
      return;
    }
    if (n !== this.seq) return;
    this.synthesisButton.hidden = !res.synthesis;
    this.rows = res.items;
    const choices = new Map([["", "All sources"]]);
    if (res.personal_source_id) choices.set(res.personal_source_id, "Personal knowledge");
    for (const source of res.sources) choices.set(source.source_id, source.root);
    const selected = choices.has(p.source_id) ? p.source_id : "";
    this.options(this.source, [...choices], selected);
    if (p.source_id && !selected) return this.load();
    this.render(p, null, res);
  }

  async synthesize() {
    const context = this.logId;
    const root = rootOf(this.box);
    const input = el("textarea", "jq");
    input.setAttribute("aria-label", "Task");
    input.maxLength = 2000;
    input.value = this.q.value;
    if (await choose("Synthesize a Knowledge guide", input, [
      { value: "run", label: "Synthesize", primary: true },
      { value: "close", label: "Close" },
    ], { root }) !== "run" || !input.value.trim()) return;

    this.synthesisButton.disabled = true;
    try {
      const { task_id } = await this.conn.call("knowledge.synthesize", {
        log_id: context, task: input.value,
      });
      const status = el("p", null, "Synthesizing…");
      const wait = choose("Knowledge synthesis", status, [
        { value: "cancel", label: "Cancel synthesis" },
      ], { root });
      let timer, settled = false;
      const result = await Promise.race([
        wait.then(() => null),
        new Promise((resolve, reject) => {
          const read = async () => {
            try {
              const result = await this.conn.call("task.status", { task_id });
              if (settled) return;
              if (["pending", "running"].includes(result.status)) timer = setTimeout(read, 500);
              else resolve(result);
            } catch (e) {
              reject(e);
            }
          };
          timer = setTimeout(read, 0);
        }),
      ]).finally(() => {
        settled = true;
        clearTimeout(timer);
        if (status.closest(".dialog:not([hidden])")) cancelAsk();
      });
      if (!result) {
        await this.conn.call("task.cancel", { task_id });
        return;
      }
      if (result.status !== "completed") throw new Error(result.error || result.status);
      await this.showGuide(JSON.parse(result.result), context, root);
    } catch (e) {
      await ask(e.message, { root, ok: "Close", cancel: "Dismiss" });
    } finally {
      this.synthesisButton.disabled = false;
    }
  }

  async showGuide(guide, context, root) {
    const body = el("div", "kconnections");
    let selected;
    for (const [label, claims] of [["Steps", guide.steps], ["Contradictions", guide.contradictions]]) {
      if (!claims.length) continue;
      const list = el(label === "Steps" ? "ol" : "ul");
      for (const claim of claims) {
        const row = el("li", null, claim.text);
        for (const number of claim.sources) {
          const citation = guide.citations[number - 1];
          row.append(" ", button("btn sm", `[${number}] ${citation.title}`, () => {
            selected = citation;
            cancelAsk();
          }));
        }
        list.append(row);
      }
      body.append(el("h3", null, label), list);
    }
    for (const gap of guide.gaps) body.append(el("p", null, gap));
    if (guide.limits.length) body.append(el("p", null, `Limits reached: ${guide.limits.join(", ")}`));
    while (true) {
      selected = null;
      await choose("Knowledge guide", body, [{ value: "close", label: "Close" }], { root });
      if (!selected) return;
      const citation = selected;
      const content = el("div", "kconnections");
      try {
        const page = await this.conn.call("knowledge.catalog", {
          entry_id: citation.id, expected_revision: citation.revision, log_id: context,
        });
        content.append(el("p", "kmeta", `${citation.relative_path} · Revision ${citation.revision}`), markdown(page.body));
      } catch (e) {
        content.append(el("p", null, e.message));
      }
      await choose(citation.title, content, [{ value: "back", label: "Back to guide" }], { root });
    }
  }

  render(p, failed = null, result = null) {
    const rows = this.rows.map((r) => this.row(r));
    if (!rows.length) {
      const what = p.query ? `Nothing matches “${p.query}”.` : this.kind === "all" ? "No skills or memories yet. An agent saves one with memory_save or skill_save, and /remember asks for one." : `No ${KINDS.find(([k]) => k === this.kind)[1]}.`;
      rows.push(el("div", "empty", failed || what));
    }
    this.list.replaceChildren(...rows);
    this.listErrors.replaceChildren(...(result?.errors || []).map(({path, error}) => el("div", "kmeta", `${path} · ${error}`)));
    const n = this.rows.length;
    this.count.textContent = failed || !n ? "" : `${result?.total > n ? `${n} of ${result.total}` : n} ${n === 1 ? "item" : "items"}`;
  }

  row(r) {
    const row = el("div", `krow k-${r.kind}`);
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.dataset.name = r.name;
    const ref = r.scope === "personal" ? r.name : r.id;
    row.dataset.ref = ref;
    row.dataset.id = r.id;
    if (ref === this.name || r.id === this.name) row.classList.add("on");
    const ic = el("span", "ic");
    ic.title = r.kind;
    ic.append(icon(r.kind === "skill" ? "play" : "book"));
    const [flag, cls, path] = flagOf(r);
    const fl = el("span", `fl ${cls}`, flag);
    if (path) fl.title = `missing: ${path}`;
    const use = el("span", "use", r.reads ? `${used(r)} · ${r.reads} ${r.reads === 1 ? "read" : "reads"}` : used(r));
    row.append(ic, el("span", "nm", r.title), use, fl, el("span", "ds", r.description));
    const meta = [this.location(r), r.expires && `Expires ${r.expires}`, r.matched_terms?.length && `Matches ${r.matched_terms.join(", ")}`, r.context_match && "Matches session context"].filter(Boolean);
    row.append(el("span", "ds", meta.join(" · ")));
    opens(row, () => this.navigate(ref));
    return row;
  }

  closeItem() {
    this.openSeq++;
    this.ed?.destroy();
    this.ed = null;
    this.name = null;
    this.detail.hidden = true;
    this.detail.replaceChildren();
    for (const x of this.list.querySelectorAll(".krow.on")) x.classList.remove("on");
  }

  async openItem(name, context = this.pageContext) {
    const n = ++this.openSeq;
    this.ed?.destroy();
    this.ed = null;
    this.note = null;
    this.item = null;
    this.name = name;
    this.pageContext = context;
    for (const x of this.list.querySelectorAll(".krow")) x.classList.toggle("on", x.dataset.ref === name || x.dataset.id === name);
    this.detail.hidden = false;
    const qualified = /^[0-9a-f]{64}:/.test(name);
    const label = qualified ? "Knowledge page" : name;
    this.detail.replaceChildren(this.head(label), el("div", "kmeta", "Loading…"));
    let it;
    try {
      const id = qualified ? name : (await this.conn.call("knowledge.get", { name })).id;
      it = await this.readPage(id, context);
    } catch (e) {
      if (n !== this.openSeq) return;
      const msg = e.code === "not_found" ? qualified ? "This page is unavailable in the selected context." : `No item named ${name} in your knowledge.` : e.message;
      this.detail.replaceChildren(this.head(label), el("div", "kmeta err", msg));
      if (!qualified && e.code === "not_found") this.drawHistory(n, name, false);
      return;
    }
    if (n !== this.openSeq) return;
    this.drawItem(n, it);
    this.detail.querySelector(".kname").focus();
  }

  // The pane's first line: Back (on a phone, where the pane replaces the
  // list), the name, and the item's kind and state.
  head(name, it = null) {
    const h = el("div", "khead");
    const back = button("btn kback", "", () => this.onBack());
    back.append(icon("left"), "All items");
    back.setAttribute("aria-label", "Back to all items");
    const title = el("div", "ktitle");
    const heading = el("h2", "kname", name);
    heading.tabIndex = -1;
    title.append(heading);
    if (it) {
      const [flag, cls] = flagOf(it);
      title.append(el("span", "kkind", it.kind), el("span", `fl ${cls}`, flag));
    }
    h.append(back, title);
    return h;
  }

  drawItem(n, it) {
    this.stamp = it.stamp;
    this.item = it;
    const facts = [used(it), `${it.reads} ${it.reads === 1 ? "read" : "reads"}`];
    for (const f of it.flags || []) facts.push(f.startsWith("stale:") ? `stale: ${f.slice(6)} is gone` : f);
    if (it.status === "proposed") facts.push("proposed by an agent: saving your edit approves it");
    const kids = [this.head(it.title, it), el("div", "kdesc", it.description), el("div", "kmeta", this.location(it))];
    kids.push(el("div", "kmeta", [it.stamp ? facts.join(" · ") : "Read-only", it.expires && `Expires ${it.expires}`, `Revision ${it.revision.slice(0, 12)}`].filter(Boolean).join(" · ")));
    if (it.files.length > 1) kids.push(el("div", "kmeta kfiles", `files: ${it.files.join(" · ")}`));
    if (!it.stamp) {
      kids.push(markdown(it.body), this.connections(it));
      this.detail.replaceChildren(...kids);
      return;
    }
    kids.push(this.connections(it));
    const host = el("div", "ked");
    this.note = el("div", "knote");
    this.note.setAttribute("aria-live", "polite");
    this.saveBtn = button("btn primary ksave", "Save", () => this.save());
    this.saveBtn.disabled = true;
    const del = button("btn kdel", "", () => this.remove(it));
    del.append(icon("trash"), "Delete");
    const acts = el("div", "kacts");
    acts.append(this.saveBtn, del, this.note);
    kids.push(host, acts);
    this.detail.replaceChildren(...kids);
    openEditor(host, {
      text: it.text,
      path: `${it.name}.md`,
      wrap: true,
      onChange: (dirty) => {
        this.saveBtn.disabled = !dirty;
        if (dirty) this.say("");
      },
      onSave: () => this.save(),
    }).then(
      (ed) => {
        if (n !== this.openSeq) return ed.destroy();
        this.ed = ed;
      },
      (e) => this.say(`The editor did not load: ${e.message}`, true),
    );
    this.drawHistory(n, it.name, true);
  }

  readPage(id, context) {
    const p = { entry_id: id };
    if (context) p.log_id = context;
    return this.conn.call("knowledge.catalog", p);
  }

  location(it, roots = {}) {
    const root = it.root || roots[it.source_id];
    return root ? `${root}/${it.relative_path}` : `Personal · ${it.relative_path}`;
  }

  connections(it) {
    const box = el("div", "kconnections");
    const links = new Map();
    const open = (id, label) => button("btn knode", label, () => this.navigate(id));
    const section = (title, nodes) => {
      const sec = el("section");
      sec.append(el("h3", null, title), nodes);
      box.append(sec);
    };
    const list = el("ul");
    for (const link of it.links) {
      const row = el("li");
      if (link.target_id) {
        row.append(open(link.target_id, link.reference));
        links.set(link.target_id, link.reference);
      } else {
        row.append(el("span", null, `${link.reference} · ${link.status === "pending" ? "Unresolved" : "Ambiguous"}`));
        for (const candidate of link.candidates) {
          const path = this.location(candidate, it.source_roots);
          const choice = open(candidate.id, path);
          const copy = button("btn sm", "Copy reference", async () => {
            try {
              await navigator.clipboard.writeText(`[[${candidate.id}]]`);
              copy.textContent = "Copied";
            } catch {
              copy.replaceWith(el("code", "kmeta", `[[${candidate.id}]]`));
            }
          });
          const option = el("div", "kchoice");
          option.append(choice, copy);
          row.append(option);
        }
      }
      list.append(row);
    }
    if (list.childElementCount) section("Outgoing links", list);
    if (it.backlinks.length) {
      const incoming = el("ul");
      for (const from of it.backlinks) {
        const row = el("li");
        row.append(open(from.id, from.title), el("span", "kmeta", this.location(from, it.source_roots)));
        incoming.append(row);
      }
      section("Incoming references", incoming);
    }
    if (it.neighbor_count) {
      const graph = el("div", "kgraph");
      const branch = (label, neighbors, direction) => {
        const side = el("div", `kbranch ${direction}`);
        side.append(el("div", "kmeta", label));
        for (const [id, title] of neighbors) if (id !== it.id) side.append(open(id, title));
        return side;
      };
      graph.append(branch("Links here", it.backlinks.map((r) => [r.id, r.title]), "incoming"), el("span", "kcenter", it.title), branch("Links from here", links, "outgoing"));
      section(`Local graph (${it.neighbor_count})`, graph);
    }
    return box;
  }

  say(text, err = false) {
    if (!this.note) return;
    this.note.replaceChildren(text);
    this.note.classList.toggle("err", err);
  }

  async save() {
    if (!this.ed?.dirty() || this.saveBtn.disabled) return;
    const seq = this.openSeq;
    const { name, id } = this.item;
    this.saveBtn.disabled = true;
    try {
      const r = await this.conn.call("knowledge.put", { name, text: this.ed.value(), stamp: this.stamp });
      if (seq !== this.openSeq) return;
      this.stamp = r.stamp;
      this.ed.markSaved();
      this.say("Saved and approved.");
      this.load();
      this.drawHistory(seq, name, true);
      this.readPage(id, this.pageContext).then((page) => {
        if (seq === this.openSeq) this.detail.querySelector(".kconnections").replaceWith(this.connections(page));
      }).catch(() => {});
    } catch (e) {
      if (seq !== this.openSeq) return;
      this.saveBtn.disabled = false;
      if (e.code === "stale") {
        this.say("It changed since you opened it, so nothing was saved. ", true);
        this.note.append(button("btn sm", "Reload", () => this.reload()));
      } else this.say(e.message, true);
    }
  }

  // The stored text again, dropping the edit (after a stale save).
  async reload() {
    const name = this.name;
    if (!(await this.leave())) return;
    if (name === this.name) this.openItem(name);
  }

  async remove(it) {
    const ok = await ask(`Delete the ${it.kind} ${it.name}? Its history keeps it, and Restore brings it back.`, { ok: "Delete" });
    if (!ok || it !== this.item) return;
    const seq = this.openSeq;
    try {
      await this.conn.call("knowledge.remove", { name: it.name });
    } catch (e) {
      if (seq === this.openSeq) this.say(e.message, true);
      return;
    }
    this.load();
    if (seq !== this.openSeq) return;
    if (it.name === this.name) this.openItem(it.name);
    else this.navigate(it.name); // The legacy route keeps History after deletion.
  }

  async drawHistory(n, name, live) {
    let res;
    try {
      res = await this.conn.call("knowledge.history", { name });
    } catch (e) {
      if (n !== this.openSeq) return;
      this.detail.querySelector(".khist")?.remove();
      this.detail.append(el("div", "khist kmeta err", e.message));
      return;
    }
    if (n !== this.openSeq) return;
    const sec = el("section", "khist");
    sec.append(el("h4", null, "History"));
    if (!res.history.length) sec.append(el("div", "kmeta", "No history: it was never saved through aegis."));
    res.history.forEach((h, i) => {
      const row = el("div", "kver");
      row.dataset.sha = h.sha;
      const what = el("div", "kvw");
      what.append(el("span", "kvm", h.message), el("span", "kvt", `${when(h.at)}${h.by ? ` · ${h.by}` : ""}`));
      row.append(what);
      // The newest version is the item as it is, and a deletion holds nothing.
      const current = i === 0 && live;
      if (!current && !h.message.startsWith("delete ")) {
        row.append(button("btn sm krestore", "Restore", () => this.restore(name, h)));
      } else if (current) row.append(el("span", "kvnow", "now"));
      sec.append(row);
    });
    this.detail.querySelector(".khist")?.remove();
    this.detail.append(sec);
  }

  async restore(name, h) {
    const seq = this.openSeq;
    if (!(await this.leave()) || seq !== this.openSeq) return;
    try {
      await this.conn.call("knowledge.restore", { name, sha: h.sha });
    } catch (e) {
      if (seq !== this.openSeq) return;
      if (this.note) this.say(e.message, true);
      else this.detail.append(el("div", "kmeta err", e.message));
      return;
    }
    this.load();
    this.openItem(name);
  }
}
