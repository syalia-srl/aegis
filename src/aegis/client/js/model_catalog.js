// Public offers and local usability, with one reviewed path to agent creation.
// Selection survives paging; provider rules save criteria, never search text.
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
};
const button = (text, action, cls = "") => {
  const b = el("button", cls, text);
  b.type = "button";
  b.onclick = action;
  return b;
};
const option = (value, label = value) => ({ value, label });
const field = (text, input) => {
  const label = el("label", "mdl-field", text);
  label.append(input);
  return label;
};
const input = (value = "") => Object.assign(el("input"), { value });
const picker = (values) => {
  const n = el("pick-chip", "pick");
  n.options = values.map(([v, t]) => option(v, t));
  n.value = values[0][0];
  return n;
};
const cost = (m) => m.free === true ? "free" : m.price_in == null || m.price_out == null ? "price unknown" : `$${m.price_in} / $${m.price_out}`;

export class Models {
  constructor(conn, root) {
    this.conn = conn;
    this.root = root;
    this.selected = new Map();
    this.rows = [];
    this.offset = 0;
    this.generation = 0;
    this.editGeneration = 0;
    this.doc = null;
    this.editing = null;
    this.timer = null;
    this.build();
  }

  build() {
    this.root.classList.add("models-page");
    const heading = el("div", "mdl-heading");
    heading.append(el("h1", "", "Models"), button("Refresh", async () => { await this.open(); if (this.editing) await this.editAgent(); }));
    this.dates = el("p", "mdl-muted");
    this.error = el("p", "mdl-error");
    const layout = el("div", "mdl-layout");
    const browse = el("div", "mdl-browse");
    this.search = input();
    this.search.type = "search";
    this.search.placeholder = "Search models";
    this.search.setAttribute("aria-label", "Search models");
    this.price = picker([["free", "Free only"], ["all", "Any price"], ["paid", "Paid"], ["unknown", "Unknown price"]]);
    this.price.setAttribute("aria-label", "Price filter");
    this.harness = picker([["opencode", "OpenCode"], ["claude-code", "Claude Code"], ["codex", "Codex"], ["cursor", "Cursor"], ["pi", "pi"]]);
    this.harness.setAttribute("aria-label", "Run with");
    this.providers = el("div", "mdl-providers");
    this.providers.setAttribute("aria-label", "Providers");
    this.providerSearch = input();
    this.providerSearch.type = "search";
    this.providerSearch.placeholder = "Find providers";
    this.providerSearch.setAttribute("aria-label", "Find providers");
    this.providerSearch.oninput = () => {
      for (const row of this.providers.children) row.hidden = !row.textContent.toLowerCase().includes(this.providerSearch.value.toLowerCase());
    };
    const providerBox = el("fieldset", "mdl-provider-field"); providerBox.append(el("legend", "", "Providers · select several"), this.providerSearch, this.providers);
    this.usable = input();
    this.usable.type = "checkbox";
    const filters = el("div", "mdl-filters");
    filters.append(this.search, this.price, field("Run with", this.harness), providerBox, field("Usable here only", this.usable));
    this.count = el("span", "mdl-muted");
    const tools = el("div", "mdl-toolbar");
    tools.append(this.count, button("Select usable shown", () => {
      for (const m of this.rows) if (m.available) this.selected.set(m.value, m);
      this.drawRows(); this.drawSelection();
    }), button("Compare selected", () => this.compare()));
    this.list = el("div", "mdl-list");
    const paging = el("div", "mdl-toolbar");
    this.prev = button("Previous", () => { this.offset = Math.max(0, this.offset - 50); this.load(); });
    this.next = button("Next", () => { this.offset += 50; this.load(); });
    paging.append(this.prev, this.next);
    browse.append(filters, tools, this.list, paging);
    const builder = el("aside", "mdl-builder");
    builder.append(el("h2", "", "Build an agent"));
    this.edit = picker([["", "New agent"]]);
    this.edit.setAttribute("aria-label", "Edit agent");
    this.name = input("free-helper");
    this.name.setAttribute("aria-label", "Agent name");
    this.mode = picker([["fixed", "Selected models"], ["dynamic", "Follow free providers"]]);
    this.mode.setAttribute("aria-label", "Model selection mode");
    this.permission = picker([["read", "Read"], ["write", "Write"], ["auto", "Auto"], ["full", "Full"]]);
    this.effort = picker([["low", "Low"], ["medium", "Medium"], ["high", "High"], ["xhigh", "Extra high"], ["max", "Max"]]);
    this.effort.value = "high";
    this.description = input();
    this.priming = el("textarea");
    this.priming.rows = 3;
    this.selection = el("div", "mdl-selection");
    this.note = el("p", "mdl-muted");
    this.reviewBtn = button("Review agent", () => this.review(), "mdl-primary needs-net");
    this.feedback = el("p", "mdl-feedback");
    builder.append(field("Existing agent", this.edit), field("Name", this.name), field("Model source", this.mode), field("Permission", this.permission), field("Effort", this.effort), field("Description", this.description), field("Instructions", this.priming), this.selection, this.note, this.reviewBtn, this.feedback);
    layout.append(browse, builder);
    this.dialog = el("dialog", "mdl-review");
    this.dialog.setAttribute("aria-label", "Review agent");
    this.root.append(heading, this.dates, this.error, layout, this.dialog);
    this.search.oninput = () => {
      clearTimeout(this.timer);
      this.timer = setTimeout(() => { this.offset = 0; this.load(); }, 200);
    };
    for (const n of [this.price, this.providers, this.usable]) n.onchange = () => { this.offset = 0; this.load(); this.drawSelection(); };
    this.harness.onchange = () => {
      this.selected.clear(); this.providers.replaceChildren(); this.providerSearch.value = ""; this.offset = 0; this.load(); this.drawSelection();
    };
    this.mode.onchange = () => this.drawSelection();
    this.edit.onchange = () => this.editAgent();
    this.drawSelection();
  }

  providerIds() { return [...this.providers.querySelectorAll("input:checked")].map((x) => x.value); }

  addProvider(p) {
    const row = el("label", "mdl-provider"); row.dataset.provider = p.id;
    const check = input(p.id); check.type = "checkbox";
    check.setAttribute("aria-label", `Provider ${p.id}`);
    row.append(check, document.createTextNode(p.name === p.id ? p.id : `${p.name} (${p.id})`));
    this.providers.append(row);
  }

  async open() {
    try {
      this.config = await this.conn.call("config.read");
      this.doc = this.config.doc;
      const value = this.edit.value;
      this.edit.options = [option("", "New agent"), ...this.doc.agents.map((a) => option(a.name))];
      this.edit.value = value;
      await this.load();
    } catch (e) { this.error.textContent = e.message; }
  }

  async load() {
    const generation = ++this.generation;
    this.error.textContent = "";
    this.count.textContent = "Loading models…";
    try {
      const r = await this.conn.call("models.browse", { harness: this.harness.value, providers: this.providerIds(), price: this.price.value, usable: this.usable.checked, search: this.search.value, offset: this.offset, limit: 50 });
      if (generation !== this.generation) return;
      const known = new Set([...this.providers.querySelectorAll("input")].map((o) => o.value));
      for (const p of r.providers) if (!known.has(p.id)) this.addProvider(p);
      this.rows = r.models;
      this.total = r.total;
      this.count.textContent = `${r.total} matching offers · ${this.selected.size} selected`;
      this.dates.textContent = `Scores: ${r.refreshed || "not available"} · Provider offers: ${r.catalog_refreshed ? new Date(r.catalog_refreshed).toLocaleString() : "not available"} · Local check: ${r.observed_at ? new Date(r.observed_at * 1000).toLocaleString() : "not available"}`;
      this.error.textContent = r.local_error || "";
      this.prev.disabled = this.offset === 0;
      this.next.disabled = r.next_offset == null;
      this.drawRows();
    } catch (e) { if (generation === this.generation) { this.error.textContent = e.message; this.count.textContent = "Could not load models"; } }
  }

  drawRows() {
    this.list.replaceChildren();
    if (!this.rows.length) this.list.append(el("p", "mdl-muted", "No matching offers. Change the filters."));
    for (const m of this.rows) {
      const row = el("label", "mdl-row");
      row.dataset.model = m.value;
      const check = input(); check.type = "checkbox"; check.checked = this.selected.has(m.value); check.disabled = !m.available;
      check.setAttribute("aria-label", `Select ${m.value}`);
      check.onchange = () => { if (check.checked) this.selected.set(m.value, m); else this.selected.delete(m.value); this.drawSelection(); };
      const name = el("span", "mdl-model");
      name.append(el("strong", "", m.label), el("code", "", m.value), el("small", m.available ? "" : "mdl-warning", m.available ? "Usable here" : m.reason));
      const scores = el("span", "mdl-score", m.rank == null ? "unranked" : `#${m.rank} · ${m.intelligence}`);
      scores.title = `Coding Index: ${m.coding ?? "unranked"}`;
      row.append(check, name, scores, el("span", m.free === true ? "mdl-free" : "mdl-price", cost(m)));
      this.list.append(row);
    }
  }

  drawSelection() {
    if (this.total != null) this.count.textContent = `${this.total} matching offers · ${this.selected.size} selected`;
    this.selection.replaceChildren();
    const dynamic = this.mode.value === "dynamic";
    this.mode.options = this.mode.options.map((o) => ({ ...o, disabled: o.value === "dynamic" && this.harness.value !== "opencode" }));
    if (dynamic) {
      this.selection.textContent = `Providers: ${this.providerIds().join(", ") || "select providers in the filter"}`;
      this.note.textContent = "Free, locally usable tool models, ranked at each new session. Search is not saved in the rule.";
    } else {
      for (const [value] of this.selected) {
        const row = el("div", "mdl-choice");
        row.append(el("code", "", value), button("Up", () => this.move(value, -1)), button("Down", () => this.move(value, 1)), button("Remove", () => { this.selected.delete(value); this.drawRows(); this.drawSelection(); }));
        row.querySelectorAll("button")[0].setAttribute("aria-label", `Move ${value} up`);
        row.querySelectorAll("button")[1].setAttribute("aria-label", `Move ${value} down`);
        this.selection.append(row);
      }
      this.note.textContent = "These exact IDs, in this order. Selection is kept across filters and pages.";
    }
    this.reviewBtn.disabled = dynamic ? !this.providerIds().length || this.price.value !== "free" || this.harness.value !== "opencode" : !this.selected.size;
    if (dynamic && this.price.value !== "free") this.note.textContent = "Choose Free only before reviewing a provider rule.";
  }

  move(value, step) {
    const entries = [...this.selected.entries()], i = entries.findIndex(([k]) => k === value), j = i + step;
    if (j < 0 || j >= entries.length) return;
    [entries[i], entries[j]] = [entries[j], entries[i]];
    this.selected = new Map(entries); this.drawSelection();
  }

  async editAgent() {
    const editGeneration = ++this.editGeneration;
    this.reviewBtn.disabled = true;
    this.edit.inert = true;
    const a = this.doc.agents.find((a) => a.name === this.edit.value);
    this.editing = a || null;
    this.name.disabled = !!a;
    this.name.value = a?.name || "free-helper";
    this.permission.value = a?.permission || "read";
    this.effort.value = a?.effort || "high";
    this.description.value = a?.description || "";
    this.priming.value = a?.priming || "";
    this.harness.value = a?.harness || "opencode";
    this.providers.replaceChildren();
    this.providerSearch.value = "";
    this.selected.clear();
    this.mode.value = a?.models && !Array.isArray(a.models) ? "dynamic" : "fixed";
    if (this.mode.value === "dynamic") this.price.value = "free";
    if (a && this.mode.value === "fixed") for (const v of (a.models?.length ? a.models : [a.model])) this.selected.set(v, { value: v });
    this.offset = 0;
    await this.load();
    if (editGeneration !== this.editGeneration) return;
    if (a && this.mode.value === "dynamic") {
      for (const id of a.models.providers) if (![...this.providers.querySelectorAll("input")].some((o) => o.value === id)) this.addProvider({ id, name: id });
      for (const o of this.providers.querySelectorAll("input")) o.checked = a.models.providers.includes(o.value);
    }
    await this.load();
    this.drawSelection();
    this.edit.inert = false;
  }

  selectionSource() { return this.mode.value === "dynamic" ? { providers: this.providerIds(), free: true } : [...this.selected.keys()]; }

  async review() {
    this.error.textContent = ""; this.feedback.textContent = "";
    const name = this.name.value.trim();
    if (!/^[a-z][a-z0-9-]*$/.test(name)) { this.error.textContent = "Use a lowercase agent name with letters, numbers and hyphens."; return; }
    try {
      const source = this.selectionSource();
      const r = await this.conn.call("models.preview", { harness: this.harness.value, models: source });
      this.pending = { ...(this.editing || {}), name, harness: this.harness.value, model: "", models: source, effort: this.effort.value, permission: this.permission.value, description: this.description.value, priming: this.priming.value || null };
      this.dialog.replaceChildren(el("h2", "", this.editing ? "Review agent changes" : "Review new agent"));
      this.dialog.append(el("p", "", `${name} · ${this.permission.value} permission · ${this.effort.value} effort`));
      this.dialog.append(el("p", "mdl-muted", Array.isArray(source) ? "Fixed cascade" : `Free-only rule: ${source.providers.join(", ")}`));
      const list = el("ol");
      for (const m of r.models) list.append(el("li", "", `${m.value} · ${cost(m)} · ${m.intelligence ?? "unranked"}`));
      this.dialog.append(list);
      if (r.excluded_total) {
        const details = el("details"); details.append(el("summary", "", `${r.excluded_total} offers excluded`));
        for (const m of r.excluded) details.append(el("p", "mdl-muted", `${m.value}: ${m.reason}`));
        this.dialog.append(details);
      }
      this.dialog.append(el("p", "mdl-muted", "Saving does not launch a session or change the default agent."));
      const actions = el("div", "mdl-toolbar");
      actions.append(button("Back", () => this.dialog.close()), button("Save agent", () => this.save(), "mdl-primary needs-net"));
      this.dialog.append(actions); this.dialog.showModal();
    } catch (e) { this.error.textContent = e.message; }
  }

  async save() {
    try {
      const r = await this.conn.call("models.save", { agent: this.pending, stamp: this.config.stamp, replace: !!this.editing });
      if (!r.saved) throw new Error(r.problems.map((x) => x.message).join("; "));
      this.config = r.config; this.doc = r.config.doc;
      this.dialog.close(); this.feedback.textContent = `Agent ${this.pending.name} saved. No session was started.`;
      await this.open();
    } catch (e) { this.dialog.close(); this.error.textContent = e.message; }
  }

  compare() {
    this.dialog.replaceChildren(el("h2", "", "Compare selected models"));
    if (!this.selected.size) this.dialog.append(el("p", "", "Select models to compare."));
    const grid = el("div", "mdl-comparison");
    for (const m of this.selected.values()) {
      const card = el("section", "mdl-compare-card");
      card.append(el("h3", "", m.label || m.value), el("code", "", m.value));
      for (const [label, value] of [["Token price / million", cost(m)], ["Price source", m.price_source || "unknown"], ["Intelligence", m.intelligence ?? "unranked"], ["Coding", m.coding ?? "unranked"], ["Context", m.window ?? "unknown"], ["Agent tools and text", m.can_agent == null ? "unknown" : m.can_agent ? "yes" : "no"], ["Usable here", m.available == null ? "refresh selection" : m.available ? "yes" : "no"]]) card.append(el("p", "", `${label}: ${value}`));
      if (m.source_url) { const link = el("a", "", "Source"); link.href = m.source_url; link.target = "_blank"; link.rel = "noopener noreferrer"; card.append(link); }
      grid.append(card);
    }
    this.dialog.append(grid, button("Close", () => this.dialog.close())); this.dialog.showModal();
  }

  destroy() { clearTimeout(this.timer); this.generation++; this.dialog.close(); }
}
