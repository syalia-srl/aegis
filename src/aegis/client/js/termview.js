// A terminal's tab (spec section 06): xterm.js where a session has its
// transcript, a box under it that sends whole lines, a row of keys a phone
// keyboard lacks, and a panel with the process. One terminal is mounted at a
// time; switching tabs disposes it, and coming back replays the snapshot.
//
// The part (parts/terminal.js) owns the markup and its nodes; this controller
// wires them and owns what a terminal does with them. It listens on the part's
// own element, never on the page, so two mounts could each hold a terminal.

import { mountTerminal } from "./terminal.js";
import { iconText } from "./glyphs.js";
import { toast } from "./toast.js";

const KEYS = { esc: "\x1b", tab: "\t", "ctrl-c": "\x03" };
// A call the person made that the server refused, such as a paste past 1 MB
// ("busy") or Restart while the program still runs ("running").
const refused = (e) => toast(e.message, { kind: "error" });

export class TermView {
  constructor(part) {
    this.part = part;
    this.live = null; // {id, ctl, history, at, conn, server, card}

    this.onKeys = (ev) => {
      const b = ev.target.closest("[data-key]");
      if (!b) return;
      ev.preventDefault();
      this.key(b.dataset.key);
    };
    this.onMode = (ev) => {
      this.part.mode.textContent = ev.detail.direct ? "direct" : "line mode";
      this.part.keys.hidden = !(ev.detail.direct && matchMedia("(pointer: coarse)").matches);
    };
    this.onSubmit = (ev) => {
      ev.preventDefault();
      this.sendBox();
    };
    // The box's own keys, as the composer answers its own (app.js): keys.js
    // acts only on its global chords inside a text field.
    this.onInput = (ev) => {
      if (ev.isComposing) return;
      if (ev.key === "Enter" && !ev.shiftKey) {
        ev.preventDefault();
        this.sendBox();
      } else if ((ev.key === "ArrowUp" || ev.key === "ArrowDown") && !ev.shiftKey) {
        if (this.history(ev.key === "ArrowUp" ? -1 : 1)) ev.preventDefault();
      } else if (ev.key === "Escape") {
        ev.preventDefault();
        ev.target.blur();
      }
    };
    this.onRestart = () => {
      if (this.live) this.live.conn.call("term.restart", { id: this.live.id }, this.live.server).catch(refused);
    };
    this.onClose = () => {
      if (this.live) this.live.conn.call("term.close", { id: this.live.id }, this.live.server).catch(refused);
    };

    part.keys.addEventListener("pointerdown", this.onKeys);
    part.el.addEventListener("termmode", this.onMode);
    part.box.addEventListener("submit", this.onSubmit);
    part.input.addEventListener("keydown", this.onInput);
    part.restart.addEventListener("click", this.onRestart);
    part.closeBtn.addEventListener("click", this.onClose);
  }

  async open(id, card, { conn, server = null } = {}) {
    if (this.live?.id === id) return this.draw(card);
    this.close();
    const mine = { id, ctl: null, history: [], at: 0, conn, server, card };
    this.live = mine;
    this.draw(card);
    const ctl = await mountTerminal(this.part.host, {
      id,
      conn,
      server,
      readOnly: card.archived === true,
      onError: refused,
    });
    if (this.live !== mine) return ctl.dispose();
    mine.ctl = ctl;
  }

  close() {
    this.live?.ctl?.dispose();
    this.live = null;
    this.part.host.replaceChildren();
    this.part.keys.hidden = true;
  }

  draw(card) {
    if (!this.live || card.id !== this.live.id) return;
    this.live.card = card;
    this.part.name.replaceChildren(...iconText("terminal", card.name));
    const state = card.state === "exited" ? `exited (${card.code})` : card.state === "ended" ? `ended: ${card.reason}` : "running";
    this.part.state.textContent = state;
    const rows = [
      ["command", card.title],
      ["pid", card.pid ?? "-"],
      ["cwd", card.cwd],
      ["started", card.started_at ? new Date(card.started_at * 1000).toLocaleString() : "-"],
      ["restart", card.restart],
      ["owner", card.owner],
    ];
    this.part.proc.replaceChildren(
      ...rows.flatMap(([k, v]) => {
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = k;
        dd.textContent = String(v ?? "-");
        return [dt, dd];
      }),
    );
    this.part.restart.hidden = card.archived === true || card.state === "running";
    this.part.closeBtn.hidden = card.archived === true;
  }

  // keys.js calls this from the term scope.
  sendBox() {
    const box = this.part.input;
    if (!this.live?.ctl || !box.value) return;
    this.live.ctl.sendLine(box.value);
    this.live.history.push(box.value);
    this.live.at = this.live.history.length;
    box.value = "";
  }

  history(step) {
    const box = this.part.input;
    if (!this.live || (box.value && this.live.at === this.live.history.length)) return false;
    this.live.at = Math.max(0, Math.min(this.live.history.length, this.live.at + step));
    box.value = this.live.history[this.live.at] ?? "";
    return true;
  }

  leaveDirect() {
    this.live?.ctl?.leave();
  }

  key(name) {
    const ctl = this.live?.ctl;
    if (!ctl) return;
    if (name === "done") return ctl.leave();
    if (name === "ctrl") return ctl.latchCtrl();
    if (name === "up" || name === "down") {
      const c = name === "up" ? "A" : "B";
      return ctl.sendKeys(ctl.appCursor ? `\x1bO${c}` : `\x1b[${c}`);
    }
    ctl.sendKeys(KEYS[name]);
  }

  destroy() {
    this.close();
    this.part.keys.removeEventListener("pointerdown", this.onKeys);
    this.part.el.removeEventListener("termmode", this.onMode);
    this.part.box.removeEventListener("submit", this.onSubmit);
    this.part.input.removeEventListener("keydown", this.onInput);
    this.part.restart.removeEventListener("click", this.onRestart);
    this.part.closeBtn.removeEventListener("click", this.onClose);
  }
}
