// What a browser_* target names on a page: role=button[name="Send"],
// text=Sign in, label=Email, or CSS. The extension runs this inside the
// person's tab, in an isolated world the page's scripts cannot reach, and the
// server's tests run it against the same fixtures as Playwright's own engine
// (tests/test_chrome_target.py), so a target that works in a server browser
// works in the person's.
//
// A classic script with one function, so the extension and the tests can
// both paste its text into a page: aegisTarget().find(selector) gives the
// elements in document order, describe(el) the role, name and box of one.

function aegisTarget() {
  const norm = (s) => (s || "").replace(/\s+/g, " ").trim();

  function hidden(el) {
    for (let e = el; e && e.nodeType === 1; e = e.parentElement) {
      if (e.getAttribute("aria-hidden") === "true" || e.hidden) return true;
      const st = getComputedStyle(e);
      if (st.display === "none") return true;
      if (e === el && st.visibility === "hidden") return true;
    }
    return false;
  }

  const INPUT = {
    button: "button", submit: "button", reset: "button", image: "button",
    checkbox: "checkbox", radio: "radio", range: "slider", number: "spinbutton",
    search: "searchbox", email: "textbox", tel: "textbox", text: "textbox",
    url: "textbox", password: "textbox", "": "textbox",
  };
  const TAGS = {
    BUTTON: "button", TEXTAREA: "textbox", OPTION: "option", UL: "list",
    OL: "list", LI: "listitem", NAV: "navigation", MAIN: "main", TABLE: "table",
    TR: "row", TD: "cell", TH: "columnheader", DIALOG: "dialog",
    ARTICLE: "article", ASIDE: "complementary", HR: "separator",
    P: "paragraph", PROGRESS: "progressbar", FORM: "form", SECTION: "region",
    HEADER: "banner", FOOTER: "contentinfo", SUMMARY: "button",
  };

  function role(el) {
    const given = (el.getAttribute("role") || "").trim().split(/\s+/)[0];
    if (given) return given;
    const tag = el.tagName;
    if (/^H[1-6]$/.test(tag)) return "heading";
    if (tag === "A" || tag === "AREA") return el.hasAttribute("href") ? "link" : "";
    if (tag === "INPUT") return INPUT[(el.getAttribute("type") || "").toLowerCase()] ?? "textbox";
    if (tag === "SELECT") return el.multiple || el.size > 1 ? "listbox" : "combobox";
    if (tag === "IMG") return el.getAttribute("alt") === "" ? "presentation" : "img";
    return TAGS[tag] || "";
  }

  function byIds(el, attr) {
    const ids = (el.getAttribute(attr) || "").split(/\s+/).filter(Boolean);
    return ids.map((id) => document.getElementById(id)).filter(Boolean);
  }

  // The text of an element as a name: its own text, with images' alt.
  function text(el) {
    let out = "";
    for (const n of el.childNodes) {
      if (n.nodeType === 3) out += n.textContent;
      else if (n.nodeType === 1 && !hidden(n)) {
        if (n.tagName === "IMG") out += " " + (n.getAttribute("alt") || "") + " ";
        else if (!["SCRIPT", "STYLE", "TEMPLATE"].includes(n.tagName)) out += " " + text(n) + " ";
      }
    }
    return out;
  }

  function labels(el) {
    const out = [];
    if (el.labels) for (const l of el.labels) out.push(norm(text(l)));
    return out.filter(Boolean);
  }

  // An approximation of the accessible name, in the order the spec takes it.
  function name(el) {
    const by = byIds(el, "aria-labelledby");
    if (by.length) return norm(by.map(text).join(" "));
    const aria = el.getAttribute("aria-label");
    if (aria && norm(aria)) return norm(aria);
    const tag = el.tagName;
    if (tag === "INPUT" && /^(button|submit|reset)$/i.test(el.type)) {
      return norm(el.value) || (el.type === "submit" ? "Submit" : el.type === "reset" ? "Reset" : "");
    }
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") {
      const l = labels(el);
      if (l.length) return l.join(" ");
      return norm(el.getAttribute("title") || el.getAttribute("placeholder") || "");
    }
    if (tag === "IMG") return norm(el.getAttribute("alt") || el.getAttribute("title") || "");
    const r = role(el);
    const fromContent = /^(button|link|heading|checkbox|radio|option|menuitem|tab|cell|columnheader|listitem|switch|treeitem|row|tooltip)$/;
    if (fromContent.test(r)) return norm(text(el)) || norm(el.getAttribute("title") || "");
    return norm(el.getAttribute("title") || "");
  }

  // A quoted value is matched whole and with its case; a bare one as part
  // of the text, in any case.
  function matcher(raw) {
    raw = raw.trim();
    const q = raw.match(/^"((?:[^"\\]|\\.)*)"(s?)$/) || raw.match(/^'((?:[^'\\]|\\.)*)'(s?)$/);
    if (q) {
      const want = q[1].replace(/\\(.)/g, "$1");
      return (s) => norm(s) === want;
    }
    const want = norm(raw).toLowerCase();
    return (s) => norm(s).toLowerCase().includes(want);
  }

  function parse(sel) {
    const m = sel.match(/^\s*(role|text|label|css)\s*=\s*([\s\S]*)$/);
    if (!m) return { kind: "css", css: sel };
    const [, kind, rest] = m;
    if (kind === "css") return { kind, css: rest };
    if (kind !== "role") {
      if (!rest.trim()) throw new Error(`${kind}= needs some text`);
      return { kind, match: matcher(rest) };
    }
    const r = rest.match(/^\s*([a-zA-Z]+)\s*((?:\[[^\]]*\]\s*)*)$/);
    if (!r) throw new Error(`cannot read the role selector ${sel}`);
    const out = { kind, role: r[1].toLowerCase(), name: null, level: null };
    for (const [, body] of r[2].matchAll(/\[([^\]]*)\]/g)) {
      const a = body.match(/^\s*([a-z-]+)\s*(?:=\s*([\s\S]*?))?\s*(i)?\s*$/);
      if (!a) throw new Error(`cannot read [${body}] in ${sel}`);
      if (a[1] === "name") {
        const v = (a[2] || "").trim();
        const quoted = /^(["']).*\1$/.test(v) ? v.slice(1, -1) : v;
        // As Playwright does: a name matches as part of the text, in any case.
        out.name = matcher(quoted);
      } else if (a[1] === "level") out.level = +a[2];
      else throw new Error(`the role selector does not take [${a[1]}]`);
    }
    return out;
  }

  const all = () => [...document.querySelectorAll("*")];

  function find(sel) {
    const p = parse(sel);
    if (p.kind === "css") {
      try {
        return [...document.querySelectorAll(p.css)];
      } catch (e) {
        throw new Error(`not a CSS selector: ${p.css}`);
      }
    }
    if (p.kind === "role") {
      return all().filter((el) => {
        if (role(el) !== p.role || hidden(el)) return false;
        if (p.level != null && +el.tagName.slice(1) !== p.level && +el.getAttribute("aria-level") !== p.level) return false;
        return p.name ? p.name(name(el)) : true;
      });
    }
    if (p.kind === "label") {
      return all().filter((el) => {
        if (!/^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName) && !el.hasAttribute("aria-label") && !el.hasAttribute("aria-labelledby")) return false;
        if (el.tagName === "INPUT" && el.type === "hidden") return false;
        const said = [...labels(el), norm(el.getAttribute("aria-label") || ""), norm(byIds(el, "aria-labelledby").map(text).join(" "))];
        return said.some((s) => s && p.match(s));
      });
    }
    // text=: the innermost elements whose text matches; buttons by value too.
    const hits = all().filter((el) => {
      if (["SCRIPT", "STYLE", "TEMPLATE", "HEAD", "TITLE", "HTML"].includes(el.tagName)) return false;
      if (el.tagName === "INPUT" && /^(button|submit|reset)$/i.test(el.type)) return p.match(el.value);
      return p.match(text(el));
    });
    return hits.filter((el) => !hits.some((o) => o !== el && el.contains(o) && o.tagName !== "INPUT"));
  }

  function describe(el) {
    const r = el.getBoundingClientRect();
    return {
      role: role(el),
      name: name(el).slice(0, 200),
      secret: el.tagName === "INPUT" && el.type === "password",
      rect: { x: r.x, y: r.y, w: r.width, h: r.height },
    };
  }

  return { find, describe, parse };
}
