// Built-in table artifact (#928). State shape from tables.build_state:
// {columns:[{id,label}], rows:[[...]], edit:null|{cells,add_rows,...},
//  submit_label, q, sort:{id,dir}|null}. The page keeps two view keys too:
// hidden (column ids the person hid) and colq (per-column filter text). Search,
// filters, column show/hide, sort and copy are always on; cell edits and
// add/remove of rows and columns only when edit allows them. Live changes go
// through aegis.state; Done submits {columns, rows}.
(() => {
  const el = (tag, cls, ...kids) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    for (const k of kids) if (k != null && k !== false) n.append(k);
    return n;
  };
  const icon = (glyph, title, onclick, cls = "") => {
    const b = el("button", `tbl-icon ${cls}`.trim(), glyph);
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-label", title);
    b.onclick = onclick;
    return b;
  };

  const isNum = (v) => {
    const s = String(v ?? "").trim();
    return s !== "" && !Number.isNaN(Number(s));
  };

  // A column reads as numeric when it has values and every non-empty one is a
  // number: then its cells are right-aligned and set in the mono face, its
  // filter takes comparisons, and it sorts numerically.
  function numericCols(state) {
    return state.columns.map((_, j) => {
      let any = false;
      for (const r of state.rows) {
        const v = String(r[j] ?? "").trim();
        if (v === "") continue;
        if (!isNum(v)) return false;
        any = true;
      }
      return any;
    });
  }

  function cellEditable(edit, id) {
    if (!edit) return false;
    if (edit.cells === true) return true;
    if (edit.cells === false) return false;
    return Array.isArray(edit.cells) && edit.cells.includes(id);
  }

  function shownColumns(state) {
    return state.columns
      .map((c, j) => ({ ...c, j }))
      .filter((c) => !(state.hidden || []).includes(c.id));
  }

  function persist(state) {
    aegis.state({
      columns: state.columns,
      rows: state.rows,
      edit: state.edit,
      submit_label: state.submit_label,
      q: state.q,
      sort: state.sort,
      hidden: state.hidden,
      colq: state.colq,
    });
  }

  // -- filters ----------------------------------------------------------
  // The filter follows the column's type. A numeric column takes a comparison
  // (>80, <=90, =85), a range (80..90 or 80-90) or a plain number (equal). Any
  // column takes /regex/ (case-insensitive unless flags are given) or, failing
  // those, a case-insensitive substring. A broken regex filters nothing and is
  // marked on its field.
  const CMP = /^(<=|>=|==|=|<|>)\s*(-?\d+(?:\.\d+)?)$/;
  const RANGE = /^(-?\d+(?:\.\d+)?)\s*(?:\.\.|-)\s*(-?\d+(?:\.\d+)?)$/;
  const NUM = /^-?\d+(?:\.\d+)?$/;
  const RX = /^\/(.+)\/([a-z]*)$/i;

  // A RegExp for /pattern/flags, null for plain text, undefined for a broken one.
  function regexOf(f) {
    const m = RX.exec(f);
    if (!m) return null;
    try {
      return new RegExp(m[1], m[2].replace(/[gy]/g, "") || "i");
    } catch {
      return undefined;
    }
  }

  function cellMatches(raw, value, num) {
    const f = String(raw || "").trim();
    if (!f) return true;
    const v = String(value ?? "").trim();
    if (num) {
      const c = CMP.exec(f);
      if (c) {
        if (!NUM.test(v)) return false;
        const x = Number(v), n = Number(c[2]);
        return { "<": x < n, "<=": x <= n, ">": x > n, ">=": x >= n, "=": x === n, "==": x === n }[c[1]];
      }
      const r = RANGE.exec(f);
      if (r) {
        if (!NUM.test(v)) return false;
        const x = Number(v), lo = Number(r[1]), hi = Number(r[2]);
        return x >= Math.min(lo, hi) && x <= Math.max(lo, hi);
      }
      if (NUM.test(f)) return NUM.test(v) && Number(v) === Number(f);
    }
    const re = regexOf(f);
    if (re === undefined) return true;
    if (re) return re.test(v);
    return v.toLowerCase().includes(f.toLowerCase());
  }

  function rowMatches(state, cells, numeric) {
    const q = (state.q || "").trim().toLowerCase();
    if (q && !cells.some((c) => String(c ?? "").toLowerCase().includes(q))) return false;
    for (const [cid, raw] of Object.entries(state.colq || {})) {
      const j = state.columns.findIndex((c) => c.id === cid);
      if (j < 0) continue;
      if (!cellMatches(raw, cells[j], numeric[j])) return false;
    }
    return true;
  }

  function visibleRows(state, numeric) {
    let rows = state.rows
      .map((cells, i) => ({ i, cells }))
      .filter(({ cells }) => rowMatches(state, cells, numeric));
    const s = state.sort;
    if (s && s.id) {
      const col = state.columns.findIndex((c) => c.id === s.id);
      if (col >= 0) {
        const dir = s.dir === "desc" ? -1 : 1;
        const num = numeric[col];
        rows = rows.slice().sort((a, b) => {
          const x = String(a.cells[col] ?? "");
          const y = String(b.cells[col] ?? "");
          if (num && x !== "" && y !== "") return (Number(x) - Number(y)) * dir;
          if (x === "") return 1;
          if (y === "") return -1;
          return x.localeCompare(y, undefined, { numeric: true, sensitivity: "base" }) * dir;
        });
      }
    }
    return rows;
  }

  // -- copy and export ----------------------------------------------------
  // What a copy shows: the visible columns and rows, in view order.
  function exportModel(state) {
    const numeric = numericCols(state);
    const cols = shownColumns(state);
    const rows = visibleRows(state, numeric).map((r) => r.cells);
    return { cols, numeric, rows };
  }

  const cellOf = (cells, c) => String(cells[c.j] ?? "");
  // A cell as a literal in JSON, YAML and TOML: a plain number stays a number,
  // anything else is a double-quoted string (JSON's escapes are valid there).
  const LIT = /^-?(?:0|[1-9]\d*)(?:\.\d+)?$/;
  const lit = (v) => (LIT.test(v) ? v : JSON.stringify(v));
  const TEX = {
    "\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", $: "\\$", "&": "\\&",
    "#": "\\#", "^": "\\textasciicircum{}", _: "\\_", "%": "\\%", "~": "\\textasciitilde{}",
  };

  const FORMATS = {
    Markdown: (m) => {
      const esc = (v) => v.replace(/\|/g, "\\|").replace(/\r?\n/g, " ");
      const head = `| ${m.cols.map((c) => esc(c.label)).join(" | ")} |`;
      const sep = `| ${m.cols.map((c) => (m.numeric[c.j] ? "---:" : "---")).join(" | ")} |`;
      const body = m.rows.map((cells) => `| ${m.cols.map((c) => esc(cellOf(cells, c))).join(" | ")} |`);
      return [head, sep, ...body].join("\n");
    },
    CSV: (m) => {
      const q = (v) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v);
      return [
        m.cols.map((c) => q(c.label)),
        ...m.rows.map((cells) => m.cols.map((c) => q(cellOf(cells, c)))),
      ]
        .map((r) => r.join(","))
        .join("\r\n");
    },
    LaTeX: (m) => {
      const tex = (v) => v.replace(/\r?\n/g, " ").replace(/[\\{}$&#^_%~]/g, (ch) => TEX[ch]);
      const line = (cells) => `${cells.map(tex).join(" & ")} \\\\`;
      return [
        `\\begin{tabular}{${m.cols.map((c) => (m.numeric[c.j] ? "r" : "l")).join("")}}`,
        "\\hline",
        line(m.cols.map((c) => c.label)),
        "\\hline",
        ...m.rows.map((cells) => line(m.cols.map((c) => cellOf(cells, c)))),
        "\\hline",
        "\\end{tabular}",
      ].join("\n");
    },
    JSON: (m) =>
      JSON.stringify(
        m.rows.map((cells) => Object.fromEntries(m.cols.map((c) => [c.id, JSON.parse(lit(cellOf(cells, c)))]))),
        null,
        2,
      ),
    YAML: (m) =>
      m.rows.length
        ? m.rows
            .map((cells) =>
              m.cols.map((c, i) => `${i ? "  " : "- "}${c.id}: ${lit(cellOf(cells, c))}`).join("\n"),
            )
            .join("\n")
        : "[]",
    TOML: (m) =>
      m.rows
        .map((cells) => ["[[rows]]", ...m.cols.map((c) => `${c.id} = ${lit(cellOf(cells, c))}`)].join("\n"))
        .join("\n\n"),
  };

  // A sandboxed frame may not write the clipboard itself. A copy made from a
  // click may use the page's copy command, though: a hidden field carries the
  // text and is removed at once. Called first, synchronously, from the click.
  function copyText(text) {
    const ta = el("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0;pointer-events:none";
    document.body.append(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }

  async function copyTo(text) {
    if (copyText(text)) return true;
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      return false;
    }
  }

  const fit = (ta) => {
    ta.style.height = "auto";
    ta.style.height = `${ta.scrollHeight}px`;
  };

  function mount(state) {
    const tools = document.getElementById("tools");
    const wrap = document.getElementById("wrap");
    const foot = document.getElementById("foot");
    if (!tools || !wrap || !foot) return;

    state.columns = state.columns || [];
    state.rows = state.rows || [];
    state.edit = state.edit || null;
    state.q = state.q || "";
    state.sort = state.sort || null;
    state.submit_label = state.submit_label || "Done";
    state.hidden = state.hidden || [];
    state.colq = state.colq || {};

    // View-only toggles, kept on the page (not persisted): the filter row
    // opens itself when a filter is already set; one menu at a time.
    let showFilters = Object.values(state.colq).some((v) => String(v || "").trim());
    let colMenuOpen = false;
    let copyMenuOpen = false;
    let copyMsg = "";
    let copyTimer = 0;

    // Undo stack of {columns, rows} snapshots, taken before each change.
    const undo = [];
    const pushUndo = (json) => {
      undo.push(json);
      if (undo.length > 50) undo.shift();
    };
    const current = () => JSON.stringify({ columns: state.columns, rows: state.rows });
    const restore = () => {
      if (!undo.length) return;
      const s = JSON.parse(undo.pop());
      state.columns = s.columns;
      state.rows = s.rows;
      persist(state);
      draw();
    };

    const focusCell = (vr, cj) => {
      const n = wrap.querySelector(`.tbl-data tbody textarea[data-pos="${vr}:${cj}"]`);
      if (n) {
        n.focus();
        n.select();
      }
    };

    const fitAll = () => wrap.querySelectorAll("textarea.tbl-cell").forEach(fit);
    window.addEventListener("resize", fitAll);

    const copyAs = async (name) => {
      const ok = await copyTo(FORMATS[name](exportModel(state)));
      copyMenuOpen = false;
      copyMsg = ok ? `Copied ${name}` : "Copy blocked";
      draw();
      clearTimeout(copyTimer);
      copyTimer = setTimeout(() => {
        copyMsg = "";
        draw();
      }, 1600);
    };

    const draw = () => {
      tools.replaceChildren();
      wrap.replaceChildren();
      foot.replaceChildren();
      const numeric = numericCols(state);
      const cols = shownColumns(state);
      const shown = visibleRows(state, numeric);

      // -- toolbar -------------------------------------------------------
      const search = el("input", "tbl-search");
      search.type = "search";
      search.placeholder = "Search all rows";
      search.setAttribute("aria-label", "Search all rows");
      search.value = state.q;
      search.addEventListener("input", () => {
        state.q = search.value;
        persist(state);
        drawBodyOnly();
      });
      tools.append(search);

      const filtersBtn = el("button", "secondary tbl-tool", "Filters");
      filtersBtn.type = "button";
      filtersBtn.setAttribute("aria-pressed", String(showFilters));
      filtersBtn.onclick = () => {
        showFilters = !showFilters;
        if (!showFilters) {
          state.colq = {};
          persist(state);
        }
        draw();
      };
      tools.append(filtersBtn);

      const colsBtn = el("button", "secondary tbl-tool", "Columns");
      colsBtn.type = "button";
      colsBtn.setAttribute("aria-expanded", String(colMenuOpen));
      colsBtn.onclick = () => {
        colMenuOpen = !colMenuOpen;
        copyMenuOpen = false;
        draw();
      };
      tools.append(colsBtn);

      const copyBtn = el("button", "secondary tbl-tool", copyMsg || "Copy \u25be");
      copyBtn.type = "button";
      copyBtn.title = "Copy the visible rows in a format";
      copyBtn.setAttribute("aria-expanded", String(copyMenuOpen));
      copyBtn.onclick = () => {
        copyMenuOpen = !copyMenuOpen;
        colMenuOpen = false;
        draw();
      };
      tools.append(copyBtn);

      if (state.edit) {
        const undoBtn = el("button", "secondary tbl-tool", "Undo");
        undoBtn.type = "button";
        undoBtn.disabled = undo.length === 0;
        undoBtn.onclick = restore;
        tools.append(undoBtn);
      }
      if (state.edit?.add_rows) {
        const b = el("button", "tbl-tool", "+ Row");
        b.type = "button";
        b.onclick = () => {
          pushUndo(current());
          state.rows.push(state.columns.map(() => ""));
          state.sort = null;
          persist(state);
          draw();
          focusCell(visibleRows(state, numericCols(state)).length - 1, cols[0]?.j ?? 0);
        };
        tools.append(b);
      }
      if (state.edit?.add_columns) {
        const b = el("button", "tbl-tool", "+ Column");
        b.type = "button";
        b.onclick = () => {
          pushUndo(current());
          let n = state.columns.length;
          let id = `c${n}`;
          while (state.columns.some((c) => c.id === id)) id = `c${++n}`;
          state.columns.push({ id, label: `Column ${n + 1}` });
          state.rows = state.rows.map((r) => r.concat([""]));
          if (Array.isArray(state.edit.cells)) state.edit.cells.push(id);
          persist(state);
          draw();
        };
        tools.append(b);
      }

      // -- copy menu -----------------------------------------------------
      if (copyMenuOpen) {
        const menu = el("div", "tbl-colmenu tbl-copymenu");
        for (const name of Object.keys(FORMATS)) {
          const b = el("button", "secondary tbl-fmt", name);
          b.type = "button";
          b.title = `Copy the visible rows as ${name}`;
          b.onclick = () => copyAs(name);
          menu.append(b);
        }
        wrap.append(menu);
      }

      // -- column show/hide menu ----------------------------------------
      if (colMenuOpen) {
        const menu = el("div", "tbl-colmenu");
        for (const c of state.columns) {
          const lbl = el("label");
          const cb = el("input");
          cb.type = "checkbox";
          cb.checked = !state.hidden.includes(c.id);
          cb.onchange = () => {
            if (cb.checked) state.hidden = state.hidden.filter((x) => x !== c.id);
            else if (shownColumns(state).length > 1) state.hidden = [...state.hidden, c.id];
            else cb.checked = true; // keep at least one column
            persist(state);
            draw();
          };
          lbl.append(cb, document.createTextNode(` ${c.label}`));
          menu.append(lbl);
        }
        wrap.append(menu);
      }

      // -- table ---------------------------------------------------------
      const scroller = el("div", "tbl-scroll");
      const table = el("table", "tbl-data");
      const thead = el("thead");
      const hr = el("tr");
      for (const col of cols) {
        const th = el("th");
        th.scope = "col";
        if (numeric[col.j]) th.className = "num";
        const btn = el("button", "tbl-sort", col.label);
        btn.type = "button";
        if (state.sort?.id === col.id) btn.dataset.dir = state.sort.dir || "asc";
        btn.onclick = () => {
          if (state.sort?.id !== col.id) state.sort = { id: col.id, dir: "asc" };
          else if (state.sort.dir === "asc") state.sort = { id: col.id, dir: "desc" };
          else state.sort = null;
          persist(state);
          draw();
        };
        th.append(btn);
        if (state.edit?.remove_columns && state.columns.length > 1)
          th.append(
            icon("\u00d7", `Remove column ${col.label}`, () => {
              pushUndo(current());
              const ix = state.columns.findIndex((c) => c.id === col.id);
              if (ix < 0) return;
              state.columns.splice(ix, 1);
              state.rows = state.rows.map((r) => r.filter((_, j) => j !== ix));
              if (state.sort?.id === col.id) state.sort = null;
              if (Array.isArray(state.edit.cells))
                state.edit.cells = state.edit.cells.filter((x) => x !== col.id);
              delete state.colq[col.id];
              persist(state);
              draw();
            }),
          );
        hr.append(th);
      }
      if (state.edit?.remove_rows) hr.append(el("th", "tbl-acts"));
      thead.append(hr);

      if (showFilters) {
        const fr = el("tr", "tbl-filters");
        for (const col of cols) {
          const th = el("th");
          const num = numeric[col.j];
          const inp = el("input");
          inp.type = "search";
          inp.placeholder = num ? "> 80, 8..9" : "text or /re/";
          inp.title = num
            ? "Numbers: >80, <=90, =85, or a range 80..90. Also /regex/ or text."
            : "Text: part of the cell. /regex/ for a pattern, e.g. /^mimo/i";
          inp.setAttribute("aria-label", `Filter ${col.label}`);
          inp.value = state.colq[col.id] || "";
          inp.classList.toggle("bad", regexOf(inp.value.trim()) === undefined);
          inp.addEventListener("input", () => {
            state.colq = { ...state.colq, [col.id]: inp.value };
            inp.classList.toggle("bad", regexOf(inp.value.trim()) === undefined);
            persist(state);
            drawBodyOnly();
          });
          th.append(inp);
          fr.append(th);
        }
        if (state.edit?.remove_rows) fr.append(el("th", "tbl-acts"));
        thead.append(fr);
      }
      table.append(thead);

      const tbody = el("tbody");
      drawRows(tbody, cols, numeric, shown);
      table.append(tbody);
      scroller.append(table);
      wrap.append(scroller);
      fitAll();

      drawFoot(shown.length);
    };

    // Re-render only the body and the count: typing in search or a column
    // filter does not rebuild the toolbar, so the field keeps focus.
    const drawBodyOnly = () => {
      const numeric = numericCols(state);
      const cols = shownColumns(state);
      const shown = visibleRows(state, numeric);
      const tbody = wrap.querySelector(".tbl-data tbody");
      if (tbody) {
        tbody.replaceChildren();
        drawRows(tbody, cols, numeric, shown);
        fitAll();
      }
      drawFoot(shown.length);
    };

    const drawRows = (tbody, cols, numeric, shown) => {
      if (!shown.length) {
        const tr = el("tr", "tbl-empty");
        const td = el("td");
        td.colSpan = cols.length + (state.edit?.remove_rows ? 1 : 0);
        td.textContent = "No rows match";
        tr.append(td);
        tbody.append(tr);
        return;
      }
      shown.forEach(({ i, cells }, vr) => {
        const tr = el("tr");
        cols.forEach((col) => {
          const j = col.j;
          const td = el("td");
          if (numeric[j]) td.className = "num";
          if (cellEditable(state.edit, col.id)) {
            // A textarea wraps long text; Enter moves down a row, Shift+Enter
            // starts a new line in the cell, Tab moves across.
            const ta = el("textarea", "tbl-cell");
            ta.rows = 1;
            ta.value = cells[j] ?? "";
            ta.dataset.pos = `${vr}:${j}`;
            ta.setAttribute("aria-label", `${col.label}, row ${i + 1}`);
            let before = null; // the state before this focus's first change
            ta.addEventListener("focus", () => {
              before = current();
            });
            ta.addEventListener("input", () => {
              if (before !== null) {
                pushUndo(before);
                before = null;
              }
              state.rows[i][j] = ta.value;
              fit(ta);
              persist(state);
            });
            ta.addEventListener("keydown", (ev) => {
              if (ev.key === "Enter" && !ev.shiftKey) {
                ev.preventDefault();
                focusCell(vr + 1, j);
              }
            });
            td.append(ta);
          } else {
            td.textContent = cells[j] ?? "";
          }
          tr.append(td);
        });
        if (state.edit?.remove_rows) {
          const td = el("td", "tbl-acts");
          td.append(
            icon("\u00d7", `Remove row ${i + 1}`, () => {
              pushUndo(current());
              state.rows.splice(i, 1);
              persist(state);
              draw();
            }),
          );
          tr.append(td);
        }
        tbody.append(tr);
      });
    };

    const drawFoot = (n) => {
      foot.replaceChildren();
      const total = state.rows.length;
      const chips = el("div", "tbl-chips");
      chips.append(
        el(
          "span",
          "tbl-count",
          n === total ? `${total} row${total === 1 ? "" : "s"}` : `${n} of ${total} rows`,
        ),
      );
      const filtered =
        (state.q || "").trim() ||
        Object.values(state.colq || {}).some((v) => String(v || "").trim());
      if (filtered) {
        const clear = el("button", "tbl-chip", "Clear filters \u00d7");
        clear.type = "button";
        clear.onclick = () => {
          state.q = "";
          state.colq = {};
          persist(state);
          draw();
        };
        chips.append(clear);
      }
      foot.append(chips);
      if (state.edit) {
        const go = el("button", "tbl-done", state.submit_label);
        go.type = "button";
        go.onclick = () =>
          aegis.submit({ columns: state.columns, rows: state.rows }, state.submit_label);
        foot.append(go);
      }
    };

    aegis.onState((next) => {
      if (!next || typeof next !== "object") return;
      Object.assign(state, next);
      draw();
    });
    draw();
  }

  window.aegisTable = { mount };
})();
