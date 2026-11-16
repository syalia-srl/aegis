// Built-in table artifact (#928). State shape from tables.build_state:
// {columns:[{id,label}], rows:[[...]], edit:null|{cells,add_rows,...},
//  submit_label, q, sort:{id,dir}|null}. The page adds, and persists, two
// view keys: hidden (column ids the person hid) and colq (per-column filter
// text). Search, per-column filters, column show/hide and sort are always on;
// cell edits and add/remove of rows and columns only when edit allows them.
// Live changes go through aegis.state; Done submits {columns, rows}.
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
  // number: then its cells are right-aligned and set in the mono face, and it
  // sorts numerically.
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

  function rowMatches(state, cells) {
    const q = (state.q || "").trim().toLowerCase();
    if (q && !cells.some((c) => String(c ?? "").toLowerCase().includes(q)))
      return false;
    for (const [cid, raw] of Object.entries(state.colq || {})) {
      const t = String(raw || "").trim().toLowerCase();
      if (!t) continue;
      const j = state.columns.findIndex((c) => c.id === cid);
      if (j < 0) continue;
      if (!String(cells[j] ?? "").toLowerCase().includes(t)) return false;
    }
    return true;
  }

  function visibleRows(state, numeric) {
    let rows = state.rows
      .map((cells, i) => ({ i, cells }))
      .filter(({ cells }) => rowMatches(state, cells));
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

  function toMarkdown(state) {
    const cols = shownColumns(state);
    const numeric = numericCols(state);
    const esc = (v) => String(v ?? "").replace(/\|/g, "\\|").replace(/\n/g, " ");
    const head = `| ${cols.map((c) => esc(c.label)).join(" | ")} |`;
    const sep = `| ${cols.map((c) => (numeric[c.j] ? "---:" : "---")).join(" | ")} |`;
    const body = visibleRows(state, numeric).map(
      ({ cells }) => `| ${cols.map((c) => esc(cells[c.j])).join(" | ")} |`,
    );
    return [head, sep, ...body].join("\n");
  }

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
    // opens itself when a column filter is already set, and the column menu
    // is closed until asked for.
    let showFilters = Object.values(state.colq).some((v) => String(v || "").trim());
    let colMenuOpen = false;

    // Undo stack of {columns, rows} snapshots for structural and cell edits.
    const undo = [];
    const snapshot = () => {
      undo.push(JSON.stringify({ columns: state.columns, rows: state.rows }));
      if (undo.length > 50) undo.shift();
    };
    const restore = () => {
      if (!undo.length) return;
      const s = JSON.parse(undo.pop());
      state.columns = s.columns;
      state.rows = s.rows;
      persist(state);
      draw();
    };

    const focusCell = (vr, cj) => {
      const sel = `.tbl-data tbody input[data-pos="${vr}:${cj}"]`;
      const n = wrap.querySelector(sel);
      if (n) {
        n.focus();
        n.select?.();
      }
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

      const colsBtn = el("button", "secondary tbl-tool", `Columns`);
      colsBtn.type = "button";
      colsBtn.setAttribute("aria-expanded", String(colMenuOpen));
      colsBtn.onclick = () => {
        colMenuOpen = !colMenuOpen;
        draw();
      };
      tools.append(colsBtn);

      const copyBtn = el("button", "secondary tbl-tool", "Copy");
      copyBtn.type = "button";
      copyBtn.title = "Copy the visible rows as a Markdown table";
      copyBtn.onclick = async () => {
        const md = toMarkdown(state);
        let ok = false;
        try {
          await navigator.clipboard.writeText(md);
          ok = true;
        } catch {
          ok = false;
        }
        if (ok) {
          copyBtn.textContent = "Copied";
          setTimeout(() => (copyBtn.textContent = "Copy"), 1200);
        } else {
          // Sandboxed frames can block the clipboard: offer the text to copy.
          const ta = el("textarea", "tbl-copybox");
          ta.readOnly = true;
          ta.value = md;
          foot.replaceChildren(ta);
          ta.focus();
          ta.select();
        }
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
        tools.append(
          (() => {
            const b = el("button", "tbl-tool", "+ Row");
            b.type = "button";
            b.onclick = () => {
              snapshot();
              state.rows.push(state.columns.map(() => ""));
              state.sort = null;
              persist(state);
              draw();
              focusCell(visibleRows(state, numericCols(state)).length - 1, cols[0]?.j ?? 0);
            };
            return b;
          })(),
        );
      }
      if (state.edit?.add_columns) {
        tools.append(
          (() => {
            const b = el("button", "tbl-tool", "+ Column");
            b.type = "button";
            b.onclick = () => {
              snapshot();
              let n = state.columns.length;
              let id = `c${n}`;
              while (state.columns.some((c) => c.id === id)) id = `c${++n}`;
              state.columns.push({ id, label: `Column ${n + 1}` });
              state.rows = state.rows.map((r) => r.concat([""]));
              if (Array.isArray(state.edit.cells)) state.edit.cells.push(id);
              persist(state);
              draw();
            };
            return b;
          })(),
        );
      }

      // -- column show/hide menu ----------------------------------------
      if (colMenuOpen) {
        const menu = el("div", "tbl-colmenu");
        for (const c of state.columns) {
          const hidden = state.hidden.includes(c.id);
          const lbl = el("label");
          const cb = el("input");
          cb.type = "checkbox";
          cb.checked = !hidden;
          cb.onchange = () => {
            if (cb.checked) state.hidden = state.hidden.filter((x) => x !== c.id);
            else if (shownColumns(state).length > 1) state.hidden = [...state.hidden, c.id];
            else cb.checked = true; // keep at least one column
            persist(state);
            draw();
          };
          lbl.append(cb, document.createTextNode(" " + c.label));
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
              snapshot();
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
          const inp = el("input");
          inp.type = "search";
          inp.placeholder = "Filter";
          inp.setAttribute("aria-label", `Filter ${col.label}`);
          inp.value = state.colq[col.id] || "";
          inp.addEventListener("input", () => {
            state.colq = { ...state.colq, [col.id]: inp.value };
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

      drawFoot(shown.length);
    };

    // Re-render only the body and the count: a keystroke in search or a
    // column filter does not rebuild the toolbar, so the field keeps focus.
    const drawBodyOnly = () => {
      const numeric = numericCols(state);
      const cols = shownColumns(state);
      const shown = visibleRows(state, numeric);
      const tbody = wrap.querySelector(".tbl-data tbody");
      if (tbody) {
        tbody.replaceChildren();
        drawRows(tbody, cols, numeric, shown);
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
            const inp = el("input", "tbl-cell");
            inp.type = "text";
            inp.value = cells[j] ?? "";
            inp.dataset.pos = `${vr}:${j}`;
            inp.setAttribute("aria-label", `${col.label}, row ${i + 1}`);
            inp.addEventListener("focus", () => snapshot(), { once: true });
            inp.addEventListener("input", () => {
              state.rows[i][j] = inp.value;
              persist(state);
            });
            inp.addEventListener("keydown", (ev) => {
              if (ev.key === "Enter" || ev.key === "ArrowDown") {
                ev.preventDefault();
                focusCell(vr + 1, j);
              } else if (ev.key === "ArrowUp") {
                ev.preventDefault();
                focusCell(vr - 1, j);
              }
            });
            td.append(inp);
          } else {
            td.textContent = cells[j] ?? "";
          }
          tr.append(td);
        });
        if (state.edit?.remove_rows) {
          const td = el("td", "tbl-acts");
          td.append(
            icon("\u00d7", `Remove row ${i + 1}`, () => {
              snapshot();
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
