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

export const FULL_HTML = `
  <div class="netbanner" id="netbanner" role="status" hidden><span class="lbl">no connection</span><span id="netbanner-t"></span><button class="btn" id="netbanner-retry" type="button">Retry now</button></div>

  <main class="view v-boot">
    <div class="boot">
      <div class="boot-mark" id="boot-mark"></div>
      <div class="notice" id="boot-text">Connecting…</div>
      <form class="login" id="login" hidden>
        <label for="login-token">Paste the token <code>aegis serve</code> printed. This browser stays signed in.</label>
        <input id="login-token" type="password" autocomplete="current-password" spellcheck="false" required>
        <button class="btn primary" type="submit">Sign in</button>
        <p class="err-text" id="login-error"></p>
      </form>
    </div>
  </main>

  <main class="view v-session">
    <div class="canvas" id="canvas" data-docked="0">
    <p class="notice wempty">This window is empty</p>
    <section class="col pane" id="tr-pane" data-pane="transcript">
      <div class="tr" id="tr" tabindex="-1"><p class="notice tr-offline" id="tr-offline" hidden>Not saved on this device. It opens when the connection is back.</p><div id="entries"></div><div class="row queued" id="queued" hidden><span class="t"></span><span class="g" id="queued-g"></span><div class="body"><span class="lbl" id="queued-lbl"></span><div class="text"></div><div class="ctl"><button class="btn primary" type="button" data-act="send">Send anyway</button><button class="btn" type="button" data-act="copy">Copy</button><button class="btn" type="button" data-act="edit">Edit</button><button class="btn" type="button" data-act="discard">Discard</button></div></div></div><div class="row replies" id="replies" hidden><span class="t"></span><span class="g" id="replies-g"></span><div class="body"><div id="replies-pills"></div><p class="cnote" id="handoff-done" hidden></p></div></div><div class="row coldnote" id="coldnote" hidden><span class="t"></span><span class="g" id="coldnote-g"></span><div class="body"><div class="cold"><span class="lbl">cache expired</span><span id="coldnote-t"></span><button class="btn needs-net" id="coldnote-go" type="button">Continue in a fresh session</button></div></div></div><div class="row closing" id="closing" hidden><span class="t"></span><span class="g" id="closing-g"></span><div class="body"><button class="btn archive needs-net" id="closing-btn" type="button" title="Close this session; it stays in the archive">Archive</button></div></div><div class="row working" id="working" hidden><span class="t"></span><span class="g" id="working-g"></span><div class="body"><span class="wverb"><span id="working-verb">Working…</span><span class="wcur"></span></span><span class="meta">(<span id="working-s">0</span>s · ↑ <span class="wtok" id="working-in">0</span> · ↓ <span class="wtok" id="working-out">0</span> tokens<span class="wesc"> · Esc to interrupt</span>)</span></div></div><div class="row working" id="waking" hidden><span class="t"></span><span class="g" id="waking-g"></span><div class="body">Waking <span class="meta">· resuming the harness</span></div></div></div>
      <div class="nav" id="nav" hidden>
        <button class="spark" id="nav-recap" title="Recap where this session stands" aria-label="Recap where this session stands"></button>
        <button class="fold" id="nav-fold" data-level="0" title="Fold tool calls and thinking (Z)" aria-label="Fold tool calls and thinking"></button>
        <button class="needs" id="nav-needs" title="Next session that needs you (Alt+J)" aria-label="Next session that needs you"><span class="n" id="nav-needs-n"></span></button>
        <button class="up" id="nav-up" title="Previous agent message (Alt+↑)" aria-label="Previous agent message"></button>
        <button class="pos" id="nav-pos" title="First unread (Alt+U)"></button>
        <button class="down" id="nav-down" title="Next agent message (Alt+↓)" aria-label="Next agent message"></button>
        <button class="last" id="nav-last" title="Start of the last agent message" aria-label="Start of the last agent message"></button>
        <button class="latest" id="jump" title="Latest (G)" aria-label="Latest"></button>
        <button class="side-btn" id="nav-side" title="Session panel (Alt+B)" aria-label="Session panel"></button>
      </div>
      <div class="find" id="find" hidden>
        <input id="find-q" spellcheck="false" autocomplete="off" placeholder="Find in transcript" aria-label="Find in this transcript">
        <span class="count" id="find-n" aria-live="polite"></span>
        <button class="prev" id="find-prev" title="Previous match (Shift+Enter)" aria-label="Previous match"></button>
        <button class="next" id="find-next" title="Next match (Enter)" aria-label="Next match"></button>
        <button class="close" id="find-close" title="Close (Esc)" aria-label="Close"></button>
      </div>
      <div class="composer" id="composer">
        <div data-slot="menu"></div>
        <div class="atts" id="atts" hidden></div>
        <div class="box">
          <textarea id="input" rows="1" placeholder="Message the agent. Enter sends, Shift+Enter adds a line, / for commands, Esc interrupts."></textarea>
          <div class="acts"><button class="mic clip needs-net" id="attach" type="button" title="Attach files" aria-label="Attach files"></button><input type="file" id="attach-pick" multiple hidden><span data-slot="mic"></span><button class="mic speak-follow" id="speak-follow" type="button" aria-pressed="false" title="Read new replies aloud" aria-label="Read new replies aloud"></button><button class="int" id="interrupt" title="Interrupt (Esc)" aria-label="Interrupt" hidden></button><button class="send" id="send" title="Send (Enter)" aria-label="Send"></button></div>
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

// Build the full layout into `root`. The mount calls it before any part is
// composed, so a part may find its nodes under the root at once.
export function buildFull(root) {
  const holder = document.createElement("template");
  holder.innerHTML = FULL_HTML;
  root.append(holder.content);
}
