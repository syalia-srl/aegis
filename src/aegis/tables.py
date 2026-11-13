"""Built-in interactive table artifacts (`table.send`).

The agent passes columns and rows (or Markdown / HTML that parses to them)
and an optional edit policy. This module validates that payload, builds the
state the page runs from, and writes the draft HTML. Landing reuses the
artifact board: same probe, card, dock and inbox as a free-form page.
"""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from . import artifacts

COL_ID = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
# GFM table: header row, separator, then body rows.
_MD_ROW = re.compile(r"^\|(.+)\|\s*$")
_MD_SEP = re.compile(r"^\|[\s:\-|]+\|\s*$")
SCRIPT_SRC = "/static/js/artifact.js"
TABLE_SRC = "/static/js/table-artifact.js"
STYLE_HREF = "/static/css/artifact.css"


class TableError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def _slug(label: str, i: int, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", label.strip().lower()).strip("_")
    if not base or not base[0].isalpha():
        base = f"c{i}"
    base = base[:32]
    if not COL_ID.match(base):
        base = f"c{i}"
    name, n = base, 2
    while name in used:
        suffix = f"_{n}"
        name = (
            (base[: 32 - len(suffix)] + suffix)
            if len(base) + len(suffix) > 32
            else base + suffix
        )
        n += 1
    used.add(name)
    return name


def normalize_columns(columns: list[Any]) -> list[dict[str, str]]:
    if not columns:
        raise TableError("bad_params", "columns must not be empty")
    if len(columns) > 64:
        raise TableError("bad_params", "at most 64 columns")
    used: set[str] = set()
    out: list[dict[str, str]] = []
    for i, c in enumerate(columns):
        if isinstance(c, str):
            label = c.strip() or f"Column {i + 1}"
            out.append({"id": _slug(label, i, used), "label": label})
            continue
        if not isinstance(c, dict):
            raise TableError(
                "bad_params", f"columns[{i}] must be a string or {{id, label}}"
            )
        label = str(c.get("label") or c.get("id") or f"Column {i + 1}").strip()
        raw = c.get("id")
        if raw is None or raw == "":
            cid = _slug(label, i, used)
        else:
            cid = str(raw)
            if not COL_ID.match(cid):
                raise TableError(
                    "bad_params",
                    f"columns[{i}].id {cid!r}: use [a-z][a-z0-9_]{{0,31}}",
                )
            if cid in used:
                raise TableError("bad_params", f"columns[{i}].id {cid!r} is repeated")
            used.add(cid)
        out.append({"id": cid, "label": label or cid})
    return out


def normalize_rows(rows: list[Any], columns: list[dict[str, str]]) -> list[list[str]]:
    if len(rows) > 2000:
        raise TableError("bad_params", "at most 2000 rows")
    ids = [c["id"] for c in columns]
    out: list[list[str]] = []
    for i, row in enumerate(rows):
        if isinstance(row, dict):
            out.append([_cell(row.get(cid, "")) for cid in ids])
        elif isinstance(row, list | tuple):
            if len(row) > len(ids):
                raise TableError(
                    "bad_params",
                    f"rows[{i}] has {len(row)} cells; the table has {len(ids)} columns",
                )
            cells = [_cell(v) for v in row]
            cells.extend([""] * (len(ids) - len(cells)))
            out.append(cells)
        else:
            raise TableError("bad_params", f"rows[{i}] must be a list or an object")
    return out


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return str(v)
    return str(v)


def normalize_edit(edit: Any, columns: list[dict[str, str]]) -> dict[str, Any] | None:
    if edit is None:
        return None
    if edit is False:
        return None
    if edit is True:
        edit = {}
    if not isinstance(edit, dict):
        raise TableError("bad_params", "edit must be an object, true, false, or null")
    ids = {c["id"] for c in columns}
    cells = edit.get("cells", True)
    if cells is True:
        cell_ids: bool | list[str] = True
    elif cells is False:
        cell_ids = False
    elif isinstance(cells, list):
        cell_ids = []
        for x in cells:
            cid = str(x)
            if cid not in ids:
                raise TableError(
                    "bad_params", f"edit.cells names unknown column {cid!r}"
                )
            if cid not in cell_ids:
                cell_ids.append(cid)
    else:
        raise TableError(
            "bad_params", "edit.cells must be true, false, or a list of column ids"
        )
    out = {
        "cells": cell_ids,
        "add_rows": bool(edit.get("add_rows", False)),
        "remove_rows": bool(edit.get("remove_rows", False)),
        "add_columns": bool(edit.get("add_columns", False)),
        "remove_columns": bool(edit.get("remove_columns", False)),
    }
    if not (
        out["cells"]
        or out["add_rows"]
        or out["remove_rows"]
        or out["add_columns"]
        or out["remove_columns"]
    ):
        raise TableError(
            "bad_params",
            "edit enables nothing: set cells, add_rows, remove_rows, add_columns, or remove_columns",
        )
    unknown = set(edit) - {
        "cells",
        "add_rows",
        "remove_rows",
        "add_columns",
        "remove_columns",
    }
    if unknown:
        raise TableError(
            "bad_params",
            f"edit has unknown keys: {', '.join(sorted(unknown))}",
        )
    return out


def parse_markdown(text: str) -> tuple[list[dict[str, str]], list[list[str]]]:
    lines = [ln.rstrip() for ln in text.strip().splitlines() if ln.strip()]
    # Find the first GFM table block.
    start = None
    for i, ln in enumerate(lines):
        if _MD_ROW.match(ln) and i + 1 < len(lines) and _MD_SEP.match(lines[i + 1]):
            start = i
            break
    if start is None:
        raise TableError(
            "bad_params", "markdown has no GFM table (header | --- | rows)"
        )
    header = _split_md_row(lines[start])
    body: list[list[str]] = []
    for ln in lines[start + 2 :]:
        if not _MD_ROW.match(ln) or _MD_SEP.match(ln):
            break
        cells = _split_md_row(ln)
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        body.append(cells[: len(header)])
    columns = normalize_columns(header)
    rows = normalize_rows(body, columns)
    return columns, rows


def _split_md_row(ln: str) -> list[str]:
    m = _MD_ROW.match(ln)
    assert m
    # Split on | but keep escaped \| as a literal pipe in the cell.
    raw = m.group(1)
    parts: list[str] = []
    buf: list[str] = []
    esc = False
    for ch in raw:
        if esc:
            buf.append(ch)
            esc = False
        elif ch == "\\":
            esc = True
        elif ch == "|":
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf).strip())
    return parts


class _TableHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_table = False
        self.in_cell = False
        self.cell: list[str] = []
        self.row: list[str] = []
        self.rows: list[list[str]] = []
        self.header: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "table":
            if self.in_table:
                return
            self.in_table = True
        elif not self.in_table:
            return
        elif t == "tr":
            self.row = []
        elif t in ("th", "td"):
            self.in_cell, self.cell = True, []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "table" and self.in_table:
            self.in_table = False
        elif not self.in_table:
            return
        elif t in ("th", "td") and self.in_cell:
            self.row.append("".join(self.cell).strip())
            self.in_cell = False
        elif t == "tr" and self.row:
            if self.header is None:
                self.header = self.row
            else:
                self.rows.append(self.row)

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)


def parse_html(text: str) -> tuple[list[dict[str, str]], list[list[str]]]:
    p = _TableHTML()
    try:
        p.feed(text)
        p.close()
    except Exception as e:
        raise TableError("bad_params", f"html is not a table: {e}") from e
    if not p.header:
        raise TableError("bad_params", "html has no <table> with a header row")
    columns = normalize_columns(p.header)
    # Pad / trim body rows to the header width.
    width = len(columns)
    body: list[list[str]] = []
    for row in p.rows:
        cells = list(row[:width]) + [""] * max(0, width - len(row))
        body.append(cells)
    return columns, normalize_rows(body, columns)


def build_state(
    *,
    columns: list[Any] | None,
    rows: list[Any] | None,
    markdown: str | None,
    html_src: str | None,
    edit: Any,
    submit_label: str,
) -> dict[str, Any]:
    sources = sum(1 for x in (markdown, html_src, columns) if x is not None)
    if markdown is not None:
        if sources != 1 or rows is not None:
            raise TableError(
                "bad_params",
                "pass markdown alone, or columns+rows, or html alone",
            )
        cols, rws = parse_markdown(markdown)
    elif html_src is not None:
        if sources != 1 or rows is not None:
            raise TableError(
                "bad_params",
                "pass html alone, or columns+rows, or markdown alone",
            )
        cols, rws = parse_html(html_src)
    else:
        if columns is None or rows is None:
            raise TableError(
                "bad_params",
                "pass columns and rows, or markdown, or html",
            )
        cols = normalize_columns(columns)
        rws = normalize_rows(rows, cols)
    policy = normalize_edit(edit, cols)
    state = {
        "columns": cols,
        "rows": rws,
        "edit": policy,
        "submit_label": submit_label,
        "q": "",
        "sort": None,
    }
    n = artifacts.json_size(state)
    if n > artifacts.MAX_STATE_BYTES:
        raise TableError(
            "too_large",
            f"table state is {n} bytes; the limit is {artifacts.MAX_STATE_BYTES}",
        )
    return state


def page_html(title: str) -> str:
    t = html.escape(title)
    return f"""\
<!doctype html>
<html lang="en">
<meta charset="utf-8">
<title>{t}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="{STYLE_HREF}">
<script src="{SCRIPT_SRC}"></script>
<script src="{TABLE_SRC}"></script>
<body>
<h2>{t}</h2>
<div id="tools" class="row tbl-tools"></div>
<div id="wrap" class="tbl-wrap"></div>
<div id="foot" class="row tbl-foot"></div>
<script>
  // table-artifact.js calls aegis.state( and aegis.submit(; named here so
  // artifacts.check sees an answer path in this file.
  aegis.ready((state, theme) => aegisTable.mount(state, theme));
</script>
</body>
</html>
"""


def write_draft(state_root: Path, id: str, title: str) -> Path:
    p = artifacts.draft_path(state_root, id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(page_html(title), encoding="utf-8")
    return p


def columns_as_dicts(state: dict[str, Any]) -> list[dict[str, str]]:
    return [{"id": c["id"], "label": c["label"]} for c in state["columns"]]


def rows_as_objects(state: dict[str, Any]) -> list[dict[str, str]]:
    ids = [c["id"] for c in state["columns"]]
    return [{cid: row[i] for i, cid in enumerate(ids)} for row in state["rows"]]
