// The full layout: the page the standalone client draws, built into the root
// the mount owns.
//
// It is the last piece of index.html's markup to leave the page. The page keeps
// its head (the theme before the first paint, the stylesheets, the manifest,
// the build meta) and an empty root; this module builds the netbanner, the boot
// view and the session view inside that root, and app.js composes the parts it
// holds: the header (b2), the session panel (b3), the views (b7), Fleet (b6)
// and the New session form (b5).
//
// The ids are the browser suite's hooks and do not change. The markup is the
// one index.html carried, id for id and in the same order, so the full suite
// proves the split by passing unchanged. The stylesheet has no rule for the
// wrapper: the root is the grid (.aegis[data-layout=full]) and this is its
// content.

import { applyLabels } from "../labels.js";

export const FULL_HTML = `
  <div class="netbanner" id="netbanner" role="status" hidden><span class="lbl" data-t="shell.noConnection"></span><span id="netbanner-t"></span><button class="btn" id="netbanner-retry" type="button" data-t="shell.retry"></button></div>

  <main class="view v-boot">
    <div class="boot">
      <div class="boot-mark" id="boot-mark"></div>
      <div class="notice" id="boot-text" data-t="shell.connecting"></div>
      <form class="login" id="login" hidden>
        <label for="login-token"><span data-t="shell.tokenBefore"></span> <code>aegis serve</code> <span data-t="shell.tokenAfter"></span></label>
        <input id="login-token" type="password" autocomplete="current-password" spellcheck="false" required>
        <button class="btn primary" type="submit" data-t="shell.signIn"></button>
        <p class="err-text" id="login-error"></p>
      </form>
    </div>
  </main>

  <main class="view v-session">
    <div class="canvas" id="canvas" data-docked="0">
    <p class="notice wempty" data-t="shell.emptyWindow"></p>
    <section class="col pane" id="tr-pane" data-pane="transcript">
      <div class="tr" id="tr" tabindex="-1"><p class="notice tr-offline" id="tr-offline" hidden data-t="transcript.offline"></p><div id="entries"></div><div class="row queued" id="queued" hidden><span class="t"></span><span class="g" id="queued-g"></span><div class="body"><span class="lbl" id="queued-lbl"></span><div class="text"></div><div class="ctl"><button class="btn primary" type="button" data-act="send" data-t="transcript.queued.send"></button><button class="btn" type="button" data-act="copy" data-t="transcript.queued.copy"></button><button class="btn" type="button" data-act="edit" data-t="transcript.queued.edit"></button><button class="btn" type="button" data-act="discard" data-t="transcript.queued.discard"></button></div></div></div><div class="row replies" id="replies" hidden><span class="t"></span><span class="g" id="replies-g"></span><div class="body"><div id="replies-pills"></div><p class="cnote" id="handoff-done" hidden></p></div></div><div class="row coldnote" id="coldnote" hidden><span class="t"></span><span class="g" id="coldnote-g"></span><div class="body"><div class="cold"><span class="lbl" data-t="transcript.cold.expired"></span><span id="coldnote-t"></span><button class="btn needs-net" id="coldnote-go" type="button" data-t="transcript.cold.continue"></button></div></div></div><div class="row closing" id="closing" hidden><span class="t"></span><span class="g" id="closing-g"></span><div class="body"><button class="btn archive needs-net" id="closing-btn" type="button" title="Close this session; it stays in the archive" data-t="transcript.closing.archive"></button></div></div><div class="row working" id="working" hidden><span class="t"></span><span class="g" id="working-g"></span><div class="body"><span class="wverb"><span id="working-verb" data-t="transcript.working.verb"></span><span class="wcur"></span></span><span class="meta"><span id="working-s">0</span><span data-t="transcript.working.seconds"></span> · ↑ <span class="wtok" id="working-in">0</span> · ↓ <span class="wtok" id="working-out">0</span> <span data-t="transcript.working.tokens"></span><span class="wesc"> · <span data-t="transcript.working.escape"></span></span></span></div></div><div class="row working" id="waking" hidden><span class="t"></span><span class="g" id="waking-g"></span><div class="body">Waking <span class="meta" data-t="transcript.waking.meta"></span></div></div></div>
      <div class="nav" id="nav" hidden>
        <button class="spark" id="nav-recap" data-t-title="transcript.nav.recapTitle" data-t-aria="transcript.nav.recap"></button>
        <button class="fold" id="nav-fold" data-level="0" data-t-title="transcript.nav.foldTitle" data-t-aria="transcript.nav.fold"></button>
        <button class="needs" id="nav-needs" data-t-title="transcript.nav.needsTitle" data-t-aria="transcript.nav.needs"><span class="n" id="nav-needs-n"></span></button>
        <button class="up" id="nav-up" data-t-title="transcript.nav.upTitle" data-t-aria="transcript.nav.up"></button>
        <button class="pos" id="nav-pos" data-t-title="transcript.nav.firstUnread"></button>
        <button class="down" id="nav-down" data-t-title="transcript.nav.downTitle" data-t-aria="transcript.nav.down"></button>
        <button class="last" id="nav-last" data-t-title="transcript.nav.lastTitle" data-t-aria="transcript.nav.last"></button>
        <button class="latest" id="jump" data-t-title="transcript.nav.latestTitle" data-t-aria="transcript.nav.latest"></button>
        <button class="side-btn" id="nav-side" data-t-title="transcript.nav.sideTitle" data-t-aria="transcript.nav.side"></button>
      </div>
      <div class="find" id="find" hidden>
        <input id="find-q" spellcheck="false" autocomplete="off" data-t-placeholder="find.placeholder" data-t-aria="find.aria">
        <span class="count" id="find-n" aria-live="polite"></span>
        <button class="prev" id="find-prev" data-t-title="find.prevTitle" data-t-aria="find.prev"></button>
        <button class="next" id="find-next" data-t-title="find.nextTitle" data-t-aria="find.next"></button>
        <button class="close" id="find-close" data-t-title="find.closeTitle" data-t-aria="find.close"></button>
      </div>
      <div class="composer" id="composer">
        <div data-slot="menu"></div>
        <div class="atts" id="atts" hidden></div>
        <div class="box">
          <textarea id="input" rows="1" data-t-placeholder="composer.placeholder.default"></textarea>
          <div class="acts"><button class="mic clip needs-net" id="attach" type="button" data-t-title="composer.attach" data-t-aria="composer.attach"></button><input type="file" id="attach-pick" multiple hidden><span data-slot="mic"></span><button class="mic speak-follow" id="speak-follow" type="button" aria-pressed="false" data-t-title="composer.speakFollow" data-t-aria="composer.speakFollow"></button><button class="int" id="interrupt" data-t-title="composer.interruptTitle" data-t-aria="composer.interrupt" hidden></button><button class="send" id="send" data-t-title="composer.sendTitle" data-t-aria="composer.send"></button></div>
        </div>
        <div class="chips">
          <span class="chip" id="chip-model"></span><span class="chip" id="chip-effort"></span><span class="chip" id="chip-perm"></span>
          <span class="err-text" id="send-error"></span>
        </div>
      </div>
      <div class="viewer" id="viewer" hidden></div>
    </section>
    </div>
  </main>
<div id="probes" hidden aria-hidden="true"></div>
`;

// Build the full layout into the mount's root. The layout's markup is here;
// its behaviour is app.js's boot, which this module loads with a dynamic
// import so a chat or tabs mount never downloads or evaluates it. The mount
// calls this after its root and socket exist, and `built()` lets the calls a
// host made while the module loaded run against the composed layout.
export async function buildFull(mount) {
  const holder = document.createElement("template");
  holder.innerHTML = FULL_HTML;
  mount.root.append(holder.content);
  applyLabels(mount.root, mount.t);
  const { composeFull } = await import("../app.js");
  if (mount.dead) return; // destroyed while the module loaded
  mount.teardown = composeFull(mount);
  mount.built();
}
