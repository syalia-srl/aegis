# aegis: `table.send`, a built-in interactive table artifact

**Status: implemented** (issue #928). Builds on
`2026-10-09-aegis-artifacts-design.md`: the page is still an artifact (same
bridge, probe, inbox, dock), but the agent does not write the HTML.

## What this delivers

An agent calls `table.send` with columns and rows (or Markdown/HTML that
parses to them) and an optional edit policy. aegis writes a built-in page,
lands it as an artifact, and the person gets filter, search, sort, and —
when the agent allowed it — cell edits and add/remove of rows or columns.
Submit returns the table; view-only pages keep UI state with `aegis.state`
and need no submit.

## Decisions

| Question | Decision | Why |
|---|---|---|
| New tool vs auto-upgrade transcript tables | New agent op `table.send` | The issue asked for an artifact/built-in page; opt-in keeps reply Markdown cheap and avoids surprising every small table |
| One-shot vs create+edit+send | One call that writes the page and lands it | The agent has no HTML to edit; mirrors `file.send` |
| How data arrives | `columns`+`rows`, or `markdown`, or `html` | Agents already have MD/HTML in the reply; structured form is what the page edits |
| Edit scope | Agent passes `edit` or omits it | Same tool for output (read + filter) and input (person edits); policy names which cells/structure may change |
| Where the page lives | Generated draft under the artifact id, same land/probe path | Reuses artifact cards, dock, `artifact_read` / `artifact_update` / `artifact_close` |
| Live edits | `aegis.state` while typing; `aegis.submit` on Done | State coalescing already exists; emit-per-keystroke would burn the rate limit |
| Look | Shared `artifact.css` + theme vars from `aegis.ready` | Matches other artifacts; no hand-rolled look per agent |

## The tool

```
table.send(
  title: str,
  columns: list[str | {id, label}] | None = None,
  rows: list[list | dict] | None = None,
  markdown: str | None = None,
  html: str | None = None,
  edit: {
    cells: true | false | list[column_id],  # default true when edit is set
    add_rows: bool = false,
    remove_rows: bool = false,
    add_columns: bool = false,
    remove_columns: bool = false,
  } | None = None,
  caption: str | None = None,
  dock: str | None = None,
  submit_label: str = "Done",
) -> {id, url, started, watching, docked?}
```

Exactly one of (`columns`+`rows`) or `markdown` or `html`. `edit` omitted
or null means view-only: filter, search, and sort still work. When `edit` is
set, a Done button submits `{columns, rows}` (current values) and collapses
the card to `submit_label`.

Caps: the built state (columns, rows, edit, UI) must fit the artifact 64 KB
limit. Column ids are `[a-z][a-z0-9_]{0,31}`; omitted ids are `c0`, `c1`, …

## Page behaviour

- Search box filters rows (case-insensitive substring across cells).
- Per-column filter inputs when there are few columns; otherwise search alone.
- Click a header to sort; again to reverse; again to clear.
- Editable cells are inputs; structural buttons appear only for allowed ops.
- Phone: tools stack; the table scrolls sideways with the first column pinned
  when wide (same idea as transcript tables).

## Out of scope for #928

- Auto-promoting Markdown tables already in a reply.
- Notes-per-row (decision cards); agents that need them keep using a free-form
  artifact.
- Spreadsheet formulas, types beyond string display, CSV upload.

## Files

- `src/aegis/tables.py` — parse, validate, render HTML
- `src/aegis/client/js/table-artifact.js` — page behaviour
- `src/aegis/client/css/artifact.css` — table chrome
- `src/aegis/artifact_ops.py` — `table.send`
- `src/aegis/help/artifacts.md` — section for agents
- `changelog.d/928-table-send.added.md`
- `tests/test_tables.py`, extend `tests/test_artifacts_e2e.py`
