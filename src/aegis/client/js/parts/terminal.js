// The terminal's tab: the <main class="view v-term"> and its pane, header,
// screen host, key row, line box and process aside that index.html used to
// hold. The part builds the markup and finds its nodes; termview.js drives
// them (open, close, draw, the box and the phone's key row).
//
// The ids stay as they are: the browser suite and termview.js address them,
// and two mounts' copies are harmless because every lookup is scoped to this
// part's element.

import { byId } from "../dom.js";
import { applyLabels } from "../labels.js";

const TERMINAL_HTML = `
<section class="term-pane">
  <header class="term-hd">
    <span class="term-name"></span><span class="term-state"></span>
    <span class="term-mode" data-t="terminal.lineMode"></span>
  </header>
  <div id="term-host" class="term-host"></div>
  <div id="term-keys" class="term-keys" hidden>
    <button type="button" data-key="esc">Esc</button>
    <button type="button" data-key="tab">Tab</button>
    <button type="button" data-key="ctrl">Ctrl</button>
    <button type="button" data-key="ctrl-c">Ctrl-C</button>
    <button type="button" data-key="up">↑</button>
    <button type="button" data-key="down">↓</button>
    <button type="button" data-key="done" data-t="terminal.done"></button>
  </div>
  <form id="term-box" class="box term-box">
    <textarea id="term-input" rows="1" data-t-placeholder="terminal.placeholder" spellcheck="false" autocapitalize="off" autocomplete="off"></textarea>
  </form>
</section>
<aside id="term-side" class="term-side">
  <dl class="term-proc"></dl>
  <div class="term-act">
    <button class="btn" type="button" id="term-restart" hidden data-t="sidebar.restart"></button>
    <button class="btn" type="button" id="term-close" data-t="terminal.close"></button>
  </div>
</aside>`;

export class TerminalPart {
  constructor(mount, container) {
    this.mount = mount;
    this.t = mount.t;
    this.root = mount.root || container;
    this.el = document.createElement("main");
    this.el.className = "view v-term";
    this.el.id = "v-term";
    this.el.setAttribute("aria-label", mount.t("terminal.aria"));
    this.el.innerHTML = TERMINAL_HTML;
    applyLabels(this.el, mount.t);
    container.append(this.el);

    const n = (id) => byId(this.el, id);
    this.pane = this.el.querySelector(".term-pane");
    this.name = this.el.querySelector(".term-name");
    this.state = this.el.querySelector(".term-state");
    this.mode = this.el.querySelector(".term-mode");
    this.host = n("term-host");
    this.keys = n("term-keys");
    this.box = n("term-box");
    this.input = n("term-input");
    this.side = n("term-side");
    this.proc = this.el.querySelector(".term-proc");
    this.restart = n("term-restart");
    this.closeBtn = n("term-close");
  }

  destroy() {
    this.el.remove();
  }
}
