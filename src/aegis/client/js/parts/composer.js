// The composer part: the box and everything that makes a send safe. One
// session's message box, its attachments (a tray of chips uploading in
// chunks), the 5 s verdict on a send, the nonce that keeps a retry from
// arriving twice, the untested-harness question, the per-session draft, and
// the outbox when there is no connection.
//
// It owns the markup index.html used to hold for it (COMPOSER_HTML), looks
// every node up under its own element and never asks the page. The mount's
// socket and callFor are its only way to the server, so two mounts on one
// page never send through each other's session.
//
// The mic and the command menu are slots, empty in a host's chat unless the
// host asks for them: with `commands` and `dictation` off, this part builds no
// menu and no mic and every code path that would use them is skipped.
// `attachments: false` is the same for files: no clip, no file input, and no
// paste or drop listener, while the tray stays for a dictation take.
//
// Every word it draws comes from the mount's label table (a7), through `t`, so
// a Spanish chat's box, its errors and its attachments read Spanish too.

import { Attachments } from "../attach.js";
import { choose, ask } from "../dialog.js";
import { byId } from "../dom.js";
import { workflowLocked, workflowTitle } from "../entries.js";
import { factIcon, icon } from "../glyphs.js";
import { idleFor } from "../lifecycle.js";
import { forKey, keyOf } from "../mount.js";
import { CommandsPart, catalogFor } from "./commands.js";
import { DictationPart } from "./dictation.js";
import { toast } from "../toast.js";

export const COMPOSER_HTML = `
<div class="composer" id="composer">
<div data-slot="menu"></div>
<div class="atts" id="atts" hidden></div>
<div class="box">
<textarea id="input" rows="1"></textarea>
<div class="acts"><button class="mic clip" id="attach" type="button"></button><input type="file" id="attach-pick" multiple hidden><span data-slot="mic"></span><button class="int" id="interrupt" hidden></button><button class="send" id="send"></button></div>
</div>
<div class="chips">
<span class="chip" id="chip-model"></span><span class="chip" id="chip-effort"></span><span class="chip" id="chip-perm"></span>
<span class="err-text" id="send-error"></span>
</div>
</div>`;

// A send gets an answer within SEND_VERDICT_MS (#350). Until then the box and
// the button are locked. Plain text with no answer by then becomes the
// session's queued draft and the socket is dropped as stalled (#429); a
// command or files go back in the box, since only text waits on the device.
const SEND_VERDICT_MS = 5000;
const LATE = new Error("no answer yet");
const CANCELLED = Symbol("cancelled");

const answered = (call) =>
  Promise.race([call, new Promise((_, reject) => setTimeout(() => reject(LATE), SEND_VERDICT_MS))]);

// Enter sends; on a touch screen it adds a line and the button sends (#707).
const touch = matchMedia("(pointer: coarse)");

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

export class ComposerPart {
  constructor(mount, host) {
    this.mount = mount;
    this.t = mount.t;
    this.key = null;
    this.menu = null; // the command menu part (b4)
    this.dictation = null; // the dictation part (b4)
    this.warmed = null; // the parked session whose first keystroke asked for session.warm
    this.spoken = new Set(); // draft keys that got dictated text since their last send
    this.sending = new Set(); // keys whose composer send waits on its answer
    this.waiting = new Set(); // keys whose send waits on its uploads
    this.unconfirmed = new Map(); // key -> {what, nonce}
    this.blocked = false;

    const holder = el("div");
    holder.innerHTML = COMPOSER_HTML;
    this.el = holder.firstElementChild;
    host.append(this.el);

    const n = (id) => byId(this.el, id);
    this.input = n("input");
    this.sendBtn = n("send");
    // A host that takes no files builds no clip and no file input. The tray
    // and its Attachments stay: a dictation take is uploaded through them,
    // and a host that wants neither turns `dictation` off too (spec 06).
    this.attachments = mount.opts.attachments !== false;
    this.attachBtn = this.attachments ? n("attach") : null;
    this.pick = this.attachments ? n("attach-pick") : null;
    if (!this.attachments) {
      n("attach")?.remove();
      n("attach-pick")?.remove();
    }
    this.attsTray = n("atts");
    this.error = n("send-error");
    this.interruptBtn = n("interrupt");
    this.outbox = mount.outbox;
    // Operator details the host may keep off a product's chat (#994). The
    // full layout asks for them, and the default is on.
    this.controls = mount.opts.controls !== false;
    if (!this.controls) {
      for (const id of ["chip-model", "chip-effort", "chip-perm"]) n(id).hidden = true;
    }

    this.icons();
    this.atts = new Attachments(this.attsTray, {
      call: (key, op, params) => this.mount.callFor(key, op, params),
      waitOnline: () => this.waitOnline(),
      t: this.t,
    });
    this.wire();
    this.ruler = this.makeRuler();
    this.observer = new ResizeObserver(([e]) => {
      if (Math.round(e.contentRect.width) === this.width) return;
      this.width = Math.round(e.contentRect.width);
      this.autosize();
    });
    this.observer.observe(this.input);
    this.outbox.setSender((key, params, opts) => this.deliver(key, params, opts));
    // The two optional parts, each into its slot under this box: a host that
    // left them off has an empty slot and no menu or mic at all.
    if (mount.opts.commands) this.menu = mount.add(this.commands());
    if (mount.opts.dictation) this.dictation = mount.add(this.dictating());
  }

  // The menu under this box: this session's catalog, the box's own line, and
  // the mount's socket for the send an overlay's Enter runs.
  commands() {
    return new CommandsPart(this.mount, this.el, {
      fetch: (key) => catalogFor(this.mount, key),
      run: (line) => this.sendLine(line, false),
      getLine: () => this.input.value,
      setLine: (v) => {
        this.input.value = v;
        this.autosize();
        if (this.key) localStorage.setItem(`aegis.draft.${this.key}`, v);
        this.input.focus();
      },
      meta: () => this.meta(),
    });
  }

  // The mic under this box: a finished take is one of its attachments, and the
  // send goes out as a held mic does (record.js).
  dictating() {
    return new DictationPart(this.mount, this.el, {
      input: this.input,
      draft: () => (this.key ? `aegis.draft.${this.key}` : null),
      heard: (key) => key && this.spoken.add(key),
      ctx: () => ({ key: this.key }),
      canStart: () => !!this.mount.conn?.open,
      prepare: () => this.mount.call("dictation.prepare"),
      // A host's speech service replaces the browser's workers.
      transcribe: this.mount.opts.transcribe,
      onTake: (file, ms, ctx) => {
        if (!ctx?.key) return;
        this.atts.add([file], ctx.key);
        if (this.key === ctx.key) this.send();
      },
    });
  }

  // The part draws its own glyphs; the sprite is in the mount's root. Its
  // words come from the mount's table, so a Spanish chat's buttons are Spanish.
  icons() {
    if (this.attachBtn) {
      this.attachBtn.title = this.t("composer.attach");
      this.attachBtn.setAttribute("aria-label", this.t("composer.attach"));
      this.attachBtn.append(icon("clip"));
    }
    this.interruptBtn.title = this.t("composer.interruptTitle");
    this.interruptBtn.setAttribute("aria-label", this.t("composer.interrupt"));
    this.sendBtn.title = this.t("composer.sendTitle");
    this.sendBtn.setAttribute("aria-label", this.t("composer.send"));
    this.sendBtn.append(icon("send"));
    this.interruptBtn.append(icon("stop"));
  }

  wire() {
    // Paste and drop are the same switch as the clip: a mount that takes no
    // files wires neither, so its box leaves the event to the host's page.
    if (this.attachments)
      this.input.addEventListener("paste", (ev) => {
        const files = this.pasted(ev);
        if (files) this.atts.add(files);
      });
    this.input.addEventListener("input", () => this.typed());
    this.input.addEventListener("keydown", (ev) => this.onKey(ev));
    this.input.addEventListener("focus", () => this.autosize());
    this.input.addEventListener("blur", () => this.autosize());
    this.sendBtn.addEventListener("click", () => this.send());
    this.interruptBtn.addEventListener("click", () => this.interrupt());
    if (this.attachments) {
      this.attachBtn.addEventListener("click", () => this.mount.conn.open && this.pick.click());
      this.pick.addEventListener("change", (ev) => {
        this.atts.add([...ev.target.files]);
        ev.target.value = "";
      });
    }
    // A tap on the box's buttons leaves the focus in the text: a blur would
    // unstack the box and move the button out from under the finger (#707).
    this.el.querySelector(".acts")?.addEventListener("mousedown", (ev) => touch.matches && ev.preventDefault());

    if (this.attachments) {
      // A file dropped on the mount belongs to its composer, and nowhere else
      // would the browser navigate away to the file.
      this.dragOver = (ev) => {
        if (!this.carriesFiles(ev)) return;
        ev.preventDefault();
        this.el.classList.toggle("dropping", true);
      };
      this.dragOut = (ev) => {
        if (ev.relatedTarget) return;
        this.el.classList.remove("dropping");
      };
      this.dropped = (ev) => {
        if (!this.carriesFiles(ev)) return;
        ev.preventDefault();
        this.el.classList.remove("dropping");
        if (this.key) this.atts.add([...ev.dataTransfer.files]);
      };
      this.mount.root.addEventListener("dragover", this.dragOver);
      this.mount.root.addEventListener("dragleave", this.dragOut);
      this.mount.root.addEventListener("drop", this.dropped);
    }
    // A connection that comes or goes changes what the box may do: the paperclip
    // needs the socket, while plain text becomes the draft instead (a6).
    this.onConnection = () => this.renderMeta();
    this.mount.on("connection", this.onConnection);
  }

  carriesFiles(ev) {
    return ev.dataTransfer?.types.includes("Files");
  }

  // A pasted screenshot is called image.png by every browser: name it by time.
  pasted(ev) {
    const got = [...(ev.clipboardData?.files || [])];
    if (!got.length) return null;
    ev.preventDefault();
    const at = new Date().toTimeString().slice(0, 8).replaceAll(":", "");
    return got.map((f, i) => {
      const ext = (f.type.split("/")[1] || "bin").replace(/[^a-z0-9]/g, "");
      return new File([f], `pasted-${at}${i ? `-${i + 1}` : ""}.${ext}`, { type: f.type });
    });
  }

  // -- the session this box writes to ---------------------------------------
  meta() {
    const raw = this.key ? this.mount.state.sessions.get(this.key) : null;
    return raw ? { ...raw, key: this.key } : null;
  }

  show(key) {
    key = key || null;
    if (key === this.key) {
      this.renderMeta();
      return;
    }
    // A recording belongs to the box it started in; moving the box to another
    // session ends it, as the page's follow() always did.
    this.dictation?.stopIfHere(this.input, "tab");
    this.key = key;
    this.atts.show(key);
    this.input.value = key ? localStorage.getItem(`aegis.draft.${key}`) || "" : "";
    this.input.disabled = !key;
    this.autosize();
    this.drawSending();
    this.renderMeta();
  }

  sessionChanged(key, meta) {
    if (key !== this.key) return;
    this.renderMeta(meta);
  }

  renderMeta(meta = this.meta()) {
    const s = meta && meta.key === this.key ? meta : this.meta();
    const working = s?.state === "working";
    const held = locked(s);
    const down = !!s?.off;
    this.el.hidden = !!s?.continued_by;
    this.interruptBtn.hidden = !working;
    this.interruptBtn.disabled = held;
    this.input.disabled = !this.key || held || down;
    if (this.attachBtn) this.attachBtn.disabled = !this.mount.conn?.open || held || down;
    this.drawSending(down || held);
    this.drawChips(s);
    this.drawHint(s);
  }

  drawChips(s) {
    const chip = (id, family, value, text, title = "") => {
      const n = byId(this.el, id);
      n.replaceChildren(...(value ? [factIcon(family, value)] : []), text);
      n.title = title;
      n.classList.toggle("click", !!this.menu);
    };
    chip("chip-model", "h", s?.harness, s?.model || "", s?.model_id || "");
    chip("chip-effort", "e", s?.effort, s ? this.t("composer.effort", { effort: s.effort }) : "");
    chip("chip-perm", "p", s?.permission, s?.permission || "");
  }

  // The placeholder: whose session, whether it is reachable, and whether a
  // message resumes it. While a parked session's harness resumes after the
  // first keystroke, the transcript's waking row says so instead.
  drawHint(s) {
    this.warming = !!(s?.asleep_why === "idle" && this.warmed === this.key);
    this.mount.transcript?.setWaking(this.warming);
    const placeholder = !s
      ? ""
      : locked(s)
        ? this.t("composer.placeholder.locked", {
            node: s.workflow.node,
            title: workflowTitle(s.workflow.run) || s.workflow.run,
          })
        : s.off
          ? this.t("composer.placeholder.off")
          : s.state === "asleep" && s.asleep_why === "idle"
            ? idleFor(s)
              ? this.t("composer.placeholder.asleepIdle", { idle: idleFor(s) })
              : this.t("composer.placeholder.asleepIdleBare")
            : s.state === "asleep"
              ? this.t("composer.placeholder.asleep")
              : touch.matches
                ? ""
                : this.t("composer.placeholder.default");
    if (this.input.placeholder !== placeholder) {
      this.input.placeholder = placeholder;
      this.autosize();
    }
  }

  // The lock on the shown session's box. `why` is renderMeta's own reason to
  // disable the button (a linked server down, someone else's session).
  drawSending(why = this.blocked) {
    this.blocked = !!why;
    const on = !!this.key && this.sending.has(this.key);
    this.el.classList.toggle("sending", on);
    this.input.readOnly = on;
    this.sendBtn.disabled = on || this.blocked;
  }

  // -- typing ----------------------------------------------------------------
  typed() {
    this.warmIfParked();
    this.autosize();
    if (this.key) localStorage.setItem(`aegis.draft.${this.key}`, this.input.value);
    if (!this.input.value.trim() && this.key) this.spoken.delete(`aegis.draft.${this.key}`);
    if (this.menu) {
      const v = this.input.value;
      if (v.startsWith("/") && !v.startsWith("//")) {
        if (this.menu.isOpen) this.menu.refresh();
        else this.menu.openInline();
      } else if (this.menu.isOpen) this.menu.close();
    }
    const now = this.input.value;
    this.el.classList.toggle("bad", now.startsWith("/") && !now.startsWith("//") && !(this.menu?.known(now) ?? true));
  }

  onKey(ev) {
    if (this.menu?.onKey(ev)) return;
    if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing && !touch.matches) {
      ev.preventDefault();
      this.send();
    }
  }

  // The first keystroke to a parked session starts its harness while the
  // person types (session.warm); once per parked stretch.
  warmIfParked() {
    const s = this.meta();
    if (!s || s.asleep_why !== "idle" || this.warmed === this.key) return;
    this.warmed = this.key;
    const key = this.key;
    this.mount.callFor(key, "session.warm", { log_id: forKey(key).log_id }).catch(() => {
      if (this.warmed !== key) return;
      this.warmed = null;
      if (this.key === key) this.drawHint(this.meta());
    });
    this.drawHint(s);
  }

  // The buttons sit beside one line of text and drop under a longer one, so
  // its lines get the box's whole width (#575). The measure is a hidden copy:
  // resizing the box to measure lays out the whole transcript again.
  makeRuler() {
    const r = document.createElement("textarea");
    r.ariaHidden = "true";
    r.tabIndex = -1;
    r.style.cssText = "position:absolute;left:-9999px;top:0;visibility:hidden;height:0;padding-top:0;padding-bottom:0;border:0;overflow:hidden;box-sizing:border-box";
    this.mount.root.append(r);
    return r;
  }

  stackActs() {
    const t = this.input;
    const box = t.closest(".box");
    if (!box.clientWidth) return box.classList.remove("stacked");
    if (touch.matches && document.activeElement === t) return box.classList.add("stacked");
    if (!t.value) return box.classList.remove("stacked");
    const css = getComputedStyle(t);
    for (const k of ["fontFamily", "fontSize", "fontWeight", "lineHeight", "letterSpacing", "paddingLeft", "paddingRight"]) this.ruler.style[k] = css[k];
    this.ruler.style.width = `${box.clientWidth - box.querySelector(".acts").offsetWidth}px`;
    this.ruler.value = t.value;
    box.classList.toggle("stacked", this.ruler.scrollHeight > parseFloat(css.lineHeight) * 1.5);
  }

  autosize() {
    if (!this.input || !this.ruler) return;
    this.stackActs();
    this.input.style.height = "auto";
    const room = window.innerHeight * 0.4;
    this.input.style.height = `${Math.min(this.input.scrollHeight, room)}px`;
    this.input.style.overflowY = this.input.scrollHeight > room ? "auto" : "hidden";
  }

  // -- sending ---------------------------------------------------------------
  // The host's call: send(text) sends that text; send() sends what the box
  // holds, with its attachments. Resolves when the send has an answer. A mount
  // with an `agent` and no session starts one on this first message.
  async send(text) {
    if (typeof text === "string") {
      if (!text.trim()) return; // nothing to send: start no session
      const key = await this.mount.ensureSession?.();
      if (!key) return;
      return this.sendLine(text, false, [], this.meta() || { key });
    }
    return this.submit();
  }

  async submit() {
    await this.dictation?.finish(this.input);
    // The text and the files as they were at Enter, so a send that waits on
    // uploads still goes where and as it was typed.
    const text = this.input.value.trim();
    const files = this.key ? this.atts.list(this.key) : [];
    // An empty box sends nothing, and with an agent set it must not start a
    // session on its own.
    if (!text && !files.length) return;
    // A host gave this mount an agent: its first message starts the session.
    await this.mount.ensureSession?.();
    const s = this.meta() || (this.key ? { key: this.key } : null);
    if (!s || this.waiting.has(s.key) || this.sending.has(s.key)) return;
    if (this.atts.list(s.key).length) {
      // Enter during an upload sends when it finishes, never without the files.
      this.waiting.add(s.key);
      this.error.textContent = this.t("composer.sending");
      try {
        await this.atts.settled(s.key);
      } finally {
        this.waiting.delete(s.key);
      }
      if (this.key === s.key) this.error.textContent = "";
    }
    const ready = this.atts.ready(s.key);
    if (ready.error) {
      toast(ready.error, { kind: "error", root: this.mount.root });
      return;
    }
    return this.sendLine(text, true, ready.ids, s);
  }

  nonceFor(key, what) {
    const u = this.unconfirmed.get(key);
    if (u?.what === what) return u.nonce;
    const nonce = crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    this.unconfirmed.set(key, { what, nonce });
    return nonce;
  }

  // A line from the box (or a reply pill). The server resolves "/" lines.
  // `target` is the session the line was typed in: a send that waited on
  // uploads may finish after the host moved the chat to another session.
  async sendLine(text, fromComposer, attachments = [], target = this.meta()) {
    const s = target;
    if ((!text && !attachments.length) || !s) return;
    const key = s.key;
    if (this.menu && (text === "/help" || text === "/workflow")) {
      // The menu is the help: it lists every command with what it does.
      const at = text === "/help" ? "/" : "/workflow ";
      if (fromComposer) this.menu.setLine(at);
      this.menu.close();
      if (fromComposer) this.menu.openInline();
      else this.menu.openOverlay(at);
      return;
    }
    if ((text === "/archive" || text === "/close") && !(await this.confirmClose(s))) return;
    if (text === "/restart" && !(await this.confirmRestart(s))) return;
    // With no way to the session's server, plain text waits as its draft on
    // this device (outbox.js); a command or files still need the connection.
    const rev = this.key === key ? this.mount.transcript?.revision() ?? -1 : -1;
    const waitAsDraft = async (nonce) => {
      await this.outbox.queue(key, text, rev, nonce);
      if (fromComposer) {
        localStorage.removeItem(`aegis.draft.${key}`);
        this.spoken.delete(`aegis.draft.${key}`); // the outbox keeps no mark
      }
      if (fromComposer && this.key === key) {
        this.input.value = "";
        this.autosize();
        this.menu?.close();
        this.el.classList.remove("bad");
      }
      if (this.key === key) this.mount.transcript?.toBottom();
    };
    if ((!this.mount.conn?.open || s.off) && !text.startsWith("/") && !attachments.length) return waitAsDraft();
    this.mount.transcript?.clearReplies(); // any send answers the turn the pills belonged to
    const params = attachments.length ? { text, attachments } : { text };
    // A linked server older than nonces would refuse the field.
    if (fromComposer && !forKey(key).server) params.nonce = this.nonceFor(key, JSON.stringify([text, attachments]));
    // Only to this server, like the nonce: a linked one older than the field would refuse it.
    if (fromComposer && !forKey(key).server && this.spoken.has(`aegis.draft.${key}`)) params.dictated = true;
    const call = this.mount.callFor(key, "session.send", params);
    const sent = (r) => {
      this.unconfirmed.delete(key);
      if (/^\/model\s/.test(text)) this.menu?.forget(key); // its efforts may differ
      if (fromComposer) {
        localStorage.removeItem(`aegis.draft.${key}`);
        this.spoken.delete(`aegis.draft.${key}`);
        this.atts.clear(key);
      }
      if (fromComposer && this.key === key) {
        this.input.value = "";
        this.autosize();
        // Clearing the box fires no input event; an open menu would take the next Esc.
        this.menu?.close();
        this.el.classList.remove("bad");
        this.error.textContent = "";
      }
      if (this.key === key) this.mount.transcript?.toBottom();
      // /tutorial and /whats-new start a guide in a new session (help.guide):
      // a chat follows it, as the full layout opens its tab.
      if (/^\/(tutorial|whats-new|doctor|handoff)(\s|$)/.test(text) && r?.log_id) {
        const far = r.server && r.server !== this.mount.conn.server ? r.server : forKey(key).server;
        this.mount.follow(keyOf(far, r.log_id));
      }
    };
    if (fromComposer) {
      this.sending.add(key);
      this.drawSending();
      if (this.key === key) this.error.textContent = "";
    }
    try {
      sent(await (fromComposer ? answered(call) : call));
    } catch (e) {
      if (e?.code === "harness_untested" && e.data) {
        // A stopped session, or a /spawn line, on an untested harness version.
        if (fromComposer) {
          this.sending.delete(key);
          this.drawSending();
        }
        try {
          const r = await this.untestedRetry(
            () => Promise.reject(e),
            (untested) => this.mount.callFor(key, "session.send", { ...params, untested }),
          );
          if (r !== CANCELLED) sent(r);
        } catch (e2) {
          this.mount.fail(key, e2);
          toast(e2.message, { kind: "error", root: this.mount.root });
        }
        return;
      }
      if (e !== LATE) {
        // A refusal the host may act on reaches it as the error event; the
        // person sees the same thing, a toast in the mount's root.
        this.mount.fail(key, e);
        toast(e.message, { kind: "error", root: this.mount.root });
        return;
      }
      // No answer yet. Plain text waits as the session's draft under the same
      // nonce, and the stalled socket is dropped so the page reconnects; the
      // draft goes out on the reconnect and arrives once.
      if (!text.startsWith("/") && !attachments.length) {
        call.catch(() => {}); // the drop rejects it; the draft carries on
        this.unconfirmed.delete(key);
        await waitAsDraft(params.nonce);
        this.mount.conn.drop();
        return;
      }
      // A command or files: the box is the person's again, with the text, and
      // says so. An answer that comes later still settles it, unless they rewrote it.
      if (this.key === key) this.error.textContent = this.t("composer.notConfirmed");
      const still = () => (this.key === key ? this.input.value.trim() : localStorage.getItem(`aegis.draft.${key}`)?.trim()) === text;
      call.then(
        (r) => still() && sent(r),
        (e) => {
          if (!still()) return;
          this.mount.fail(key, e);
          if (this.key === key) this.error.textContent = this.t("composer.notSent", { error: e.message });
        },
      );
    } finally {
      if (fromComposer) {
        this.sending.delete(key);
        this.drawSending();
      }
    }
  }

  async interrupt() {
    const s = this.meta();
    if (!s || s.state !== "working") return;
    try {
      await this.mount.callFor(s.key, "session.interrupt");
    } catch (e) {
      toast(e.message, { kind: "error", root: this.mount.root });
    }
  }

  // -- the untested-harness question ---------------------------------------
  // A harness version outside the tested range (harness_versions.py): the
  // server refused the launch once with what it found; ask the person whether
  // to start once, allow that version for good, or cancel. Resolves "once",
  // "always" or null.
  untestedAsk(d) {
    const p = (cls, text) => el("p", cls, text);
    const body = el("div", "untested");
    const range = d.min === d.max ? d.max : this.t("composer.untested.between", { min: d.min, max: d.max });
    const head = d.state === "unknown" ? this.t("composer.untested.unknownVersion", { label: d.label }) : `${d.label} ${d.version}`;
    body.append(p("range", this.t("composer.untested.range", { head, range })));
    body.append(p("text", (d.message || "").split("\n")[0]));
    body.append(p("found", this.t("composer.untested.found", { path: d.path || this.t("composer.untested.nothing") })));
    const cmd = el("div", "cmd");
    const code = document.createElement("code");
    code.textContent = d.install;
    const copy = el("button", "btn copy-cmd", this.t("composer.untested.copy"));
    copy.type = "button";
    copy.addEventListener("click", () =>
      navigator.clipboard.writeText(d.install).then(
        () => toast(this.t("composer.untested.copied"), { root: this.mount.root }),
        () => toast(this.t("composer.untested.copyFailed"), { kind: "error", root: this.mount.root }),
      ),
    );
    cmd.append(code, copy);
    body.append(cmd);
    for (const note of d.notes || []) body.append(p("note", note));
    const buttons = [{ label: this.t("composer.untested.cancel"), value: null }];
    if (d.may_always) buttons.push({ label: this.t("composer.untested.always", { version: d.version }), value: "always" });
    buttons.push({ label: this.t("composer.untested.startOnce"), value: "once", primary: true });
    const title =
      d.state === "unknown"
        ? this.t("composer.untested.titleUnknown", { label: d.label })
        : this.t("composer.untested.title", { label: d.label, version: d.version });
    return choose(title, body, buttons, { root: this.mount.root });
  }

  // A call that starts a harness, asked again with the person's answer when
  // the server refuses it as harness_untested. Cancel resolves CANCELLED,
  // which no answer can be: a send answers a delivery with nothing at all.
  async untestedRetry(call, again) {
    try {
      return await call();
    } catch (e) {
      if (e.code !== "harness_untested" || !e.data) throw e;
      const pick = await this.untestedAsk(e.data);
      if (!pick) return CANCELLED;
      const r = await again(pick);
      if (r?.not_saved) toast(this.t("composer.startedOnce", { error: r.not_saved }), { kind: "error", root: this.mount.root });
      return r;
    }
  }

  // The queued draft's sender: the composer's deadline, and the person's
  // answer when a background flush meets an untested harness.
  deliver(key, params, { ask: asking = false } = {}) {
    const call = this.mount.callFor(key, "session.send", params);
    const late = setTimeout(() => this.mount.conn.drop(), SEND_VERDICT_MS);
    return call
      .finally(() => clearTimeout(late))
      .catch(async (e) => {
        if (!asking || e.code !== "harness_untested" || !e.data) throw e;
        const pick = await this.untestedAsk(e.data);
        if (!pick) throw e;
        return this.mount.callFor(key, "session.send", { ...params, untested: pick });
      });
  }

  // -- the two questions a "/" line asks ------------------------------------
  confirmClose(s) {
    return (
      s.attention === "done" ||
      ask(this.t("composer.archive.question", { title: s.title || s.handle }), {
        ok: this.t("composer.archive.ok"),
        root: this.mount.root,
      })
    );
  }

  confirmRestart(s) {
    return (
      s.state !== "working" ||
      ask(this.t("composer.restart.question", { handle: s.handle }), {
        ok: this.t("composer.restart.ok"),
        root: this.mount.root,
      })
    );
  }

  // A queued draft put back in the box (the transcript's Edit).
  edit(text) {
    this.input.value = this.input.value ? `${text}\n\n${this.input.value}` : text;
    if (this.key) localStorage.setItem(`aegis.draft.${this.key}`, this.input.value);
    this.autosize();
    this.input.focus();
  }

  waitOnline() {
    return new Promise((resolve) => {
      const t = setInterval(() => {
        if (this.mount.conn?.open) {
          clearInterval(t);
          resolve();
        }
      }, 500);
    });
  }

  destroy() {
    this.observer?.disconnect();
    this.mount.root.removeEventListener("dragover", this.dragOver);
    this.mount.root.removeEventListener("dragleave", this.dragOut);
    this.mount.root.removeEventListener("drop", this.dropped);
    this.mount.listeners.get("connection")?.delete(this.onConnection);
    this.ruler?.remove();
  }
}

// A session whose workflow node is the engine's while the run lives (entries.js).
function locked(s) {
  return workflowLocked(s) || !!s?.continued_by;
}
