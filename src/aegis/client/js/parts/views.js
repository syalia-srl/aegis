// The views that are not the Fleet, a session or the new-session form:
// Settings, Journal, Usage, Knowledge, Files, Schedules and Help, with the
// three overlays the page draws over them (the ? key list, the command palette
// and Alt+O's file picker).
//
// Each view is a <main class="view v-x"> with the section its module draws in,
// built here and found here. The router still decides which one is showing by
// the root's data-view; the part only gives each module the node it owns, so
// no view module reaches into the page for an id.

// One view's shell: the main element the layout shows, and the section the
// module constructor receives. `box` is what a module keeps as its root.
class ViewPart {
  constructor(host, { view, cls, id, before = null }) {
    this.el = document.createElement("main");
    this.el.className = `view ${view}`;
    this.box = document.createElement("section");
    this.box.className = cls;
    this.box.id = id;
    this.el.append(this.box);
    host.insertBefore(this.el, before);
  }

  destroy() {
    this.el.remove();
  }
}

// The overlays the page draws over any view. Their ids stay as they are: the
// browser suite and keys.js address them.
const OVERLAYS_HTML = `
<div class="keymap" id="keymap" hidden></div>
<div class="palette" id="palette" hidden></div>
<div class="palette fpick" id="fpick" hidden></div>`;

const VIEWS = [
  ["settings", "v-settings", "settings", "settings"],
  ["journal", "v-journal", "journal", "journal"],
  ["usage", "v-usage", "usage", "usage"],
  ["knowledge", "v-knowledge", "knowledge", "knowledge"],
  ["files", "v-files", "files", "files"],
  ["schedules", "v-schedules", "schedules", "schedules"],
  ["help", "v-help", "helpv", "help"],
];

export class ViewsPart {
  // `before` is the view these mains are inserted ahead of (the session's), so
  // the page keeps the order index.html had while that markup is still there.
  constructor(mount, host, { before = null } = {}) {
    this.mount = mount;
    this.root = mount.root || host;
    this.parts = [];
    for (const [name, view, cls, id] of VIEWS) {
      const part = new ViewPart(host, { view, cls, id, before });
      this.parts.push(part);
      this[name] = part.box;
    }

    const overlays = document.createElement("template");
    overlays.innerHTML = OVERLAYS_HTML;
    this.overlays = [...overlays.content.children];
    for (const overlay of this.overlays) host.append(overlay);
    [this.keymap, this.palette, this.fpick] = this.overlays;
  }

  destroy() {
    for (const part of this.parts) part.destroy();
    for (const overlay of this.overlays) overlay.remove();
  }
}
