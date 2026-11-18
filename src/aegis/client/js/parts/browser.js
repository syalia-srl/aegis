// The browser's tab: the <main class="view v-browser"> and the host a streamed
// page mounts in, which index.html used to hold. The part builds the markup
// and finds its node; browserview.js's BrowserTab drives it.
//
// The id stays as it is: the browser suite addresses it, and two mounts'
// copies are harmless because every lookup is scoped to this part's element.

import { byId } from "../dom.js";
import { labelsFor } from "../labels.js";

export class BrowserPart {
  constructor(mount, container) {
    this.mount = mount;
    this.t = mount.t || labelsFor("en");
    this.root = mount.root || container;
    this.el = document.createElement("main");
    this.el.className = "view v-browser";
    this.el.id = "v-browser";
    this.el.setAttribute("aria-label", this.t("browser.aria"));
    this.el.innerHTML = `<div id="br-tab" class="br-host"></div>`;
    container.append(this.el);
    this.host = byId(this.el, "br-tab");
  }

  destroy() {
    this.el.remove();
  }
}
