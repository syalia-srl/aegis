// Built-in table artifact (#928). State shape from tables.build_state:
// {columns:[{id,label}], rows:[[...]], edit:null|{cells,add_rows,...},
//  submit_label, q, sort:{id,dir}|null}. Filter/search/sort always; edits
// only when edit is set. Live changes go through aegis.state; Done submits.
(() => {
  const el = (tag, cls, ...kids) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    for (const k of kids) if (k != null && k !== false) n.append(k);
    return n;
  };

  function cellEditable(edit, id) {
    if (!edit) return false;
    if (edit.cells === true) return true;
    if (edit.cells === false) return false;
    return Array.isArray(edit.cells) && edit.cells.includes(id);
  }

  function persist(state) {
    aegis.state({
      columns: state.columns,
      rows: state.rows,
      edit: state.edit,
      submit_label: state.submit_label,
      q: state.q,
      sort: state.sort,
    });
  }

  function visibleRows(state) {
    const q = (state.q || "").trim().toLowerCase();
    let rows = state.rows.map((cells, i) => ({ i, cells }));
    if (q) {
      rows = rows.filter(({ cells }) =>
        cells.some((c) => String(c).toLowerCase().includes(q)),
      );
    }
    const s = state.sort;
    if (s && s.id) {
      const col = state.columns.findIndex((c) => c.id === s.id);
      if (col >= 0) {
        const dir = s.dir === "desc" ? -1 : 1;
        rows = rows.slice().sort((a, b) => {
          const x = String(a.cells[col] ?? "");
          const y = String(b.cells[col] ?? "");
          const nx = Number(x),
            ny = Number(y);
          if (x !== "" && y !== "" && !Number.isNaN(nx) && !Number.isNaN(ny))
            return (nx - ny) * dir;
          return x.localeCompare(y, undefined, { sensitivity: "base" }) * dir;
        });
      }
    }
    return rows;
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

    const draw = () => {
      tools.replaceChildren();
      wrap.replaceChildren();
      foot.replaceChildren();

      const search = el("input");
      search.type = "search";
      search.placeholder = "Search rows";
      search.setAttribute("aria-label", "Search rows");
      search.value = state.q;
      search.addEventListener("input", () => {
        state.q = search.value;
        persist(state);
        draw();
      });
      tools.append(search);

      if (state.edit?.add_rows) {
        const add = el("button", "secondary", "Add row");
        add.type = "button";
        add.onclick = () => {
          state.rows.push(state.columns.map(() => ""));
          persist(state);
          draw();
        };
        tools.append(add);
      }
      if (state.edit?.add_columns) {
        const add = el("button", "secondary", "Add column");
        add.type = "button";
        add.onclick = () => {
          let n = state.columns.length;
          let id = `c${n}`;
          while (state.columns.some((c) => c.id === id)) id = `c${++n}`;
          state.columns.push({ id, label: `Column ${n + 1}` });
          state.rows = state.rows.map((r) => r.concat([""]));
          persist(state);
          draw();
        };
        tools.append(add);
      }

      const scroller = el("div", "tbl-scroll");
      const table = el("table", "tbl-data");
      const thead = el("thead");
      const hr = el("tr");
      for (const col of state.columns) {
        const th = el("th");
        th.scope = "col";
        const btn = el("button", "tbl-sort", col.label);
        btn.type = "button";
        if (state.sort?.id === col.id)
          btn.dataset.dir = state.sort.dir || "asc";
        btn.onclick = () => {
          if (state.sort?.id !== col.id) state.sort = { id: col.id, dir: "asc" };
          else if (state.sort.dir === "asc") state.sort = { id: col.id, dir: "desc" };
          else state.sort = null;
          persist(state);
          draw();
        };
        th.append(btn);
        if (state.edit?.remove_columns && state.columns.length > 1) {
          const rm = el("button", "tbl-icon", "×");
          rm.type = "button";
          rm.title = `Remove ${col.label}`;
          rm.setAttribute("aria-label", `Remove column ${col.label}`);
          rm.onclick = () => {
            const ix = state.columns.findIndex((c) => c.id === col.id);
            if (ix < 0) return;
            state.columns.splice(ix, 1);
            state.rows = state.rows.map((r) => r.filter((_, j) => j !== ix));
            if (state.sort?.id === col.id) state.sort = null;
            if (Array.isArray(state.edit.cells))
              state.edit.cells = state.edit.cells.filter((x) => x !== col.id);
            persist(state);
            draw();
          };
          th.append(rm);
        }
        hr.append(th);
      }
      if (state.edit?.remove_rows) hr.append(el("th", "tbl-acts"));
      thead.append(hr);
      table.append(thead);

      const tbody = el("tbody");
      const shown = visibleRows(state);
      for (const { i, cells } of shown) {
        const tr = el("tr");
        state.columns.forEach((col, j) => {
          const td = el("td");
          if (cellEditable(state.edit, col.id)) {
            const inp = el("input");
            inp.type = "text";
            inp.value = cells[j] ?? "";
            inp.setAttribute("aria-label", `${col.label}, row ${i + 1}`);
            inp.addEventListener("input", () => {
              state.rows[i][j] = inp.value;
              persist(state);
            });
            td.append(inp);
          } else {
            td.textContent = cells[j] ?? "";
          }
          tr.append(td);
        });
        if (state.edit?.remove_rows) {
          const td = el("td", "tbl-acts");
          const rm = el("button", "tbl-icon", "×");
          rm.type = "button";
          rm.title = "Remove row";
          rm.setAttribute("aria-label", `Remove row ${i + 1}`);
          rm.onclick = () => {
            state.rows.splice(i, 1);
            persist(state);
            draw();
          };
          td.append(rm);
          tr.append(td);
        }
        tbody.append(tr);
      }
      table.append(tbody);
      scroller.append(table);
      wrap.append(scroller);

      const n = shown.length;
      const total = state.rows.length;
      foot.append(
        el(
          "span",
          "tbl-count",
          n === total ? `${total} row${total === 1 ? "" : "s"}` : `${n} of ${total} rows`,
        ),
      );
      if (state.edit) {
        const go = el("button", null, state.submit_label);
        go.type = "button";
        go.onclick = () =>
          aegis.submit(
            { columns: state.columns, rows: state.rows },
            state.submit_label,
          );
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
