"""Pure table.send helpers: parse, edit policy, state size, page HTML."""

import pytest

from aegis import artifacts, tables
from aegis.tables import TableError


def test_normalize_columns_and_rows():
    cols = tables.normalize_columns(["Name", {"id": "age", "label": "Age"}])
    assert cols == [
        {"id": "name", "label": "Name"},
        {"id": "age", "label": "Age"},
    ]
    rows = tables.normalize_rows([["Ada", 36], {"name": "Grace", "age": "40"}], cols)
    assert rows == [["Ada", "36"], ["Grace", "40"]]


def test_parse_markdown_and_html():
    md = """\
| A | B |
| --- | --- |
| 1 | 2 |
| 3 | 4 |
"""
    cols, rows = tables.parse_markdown(md)
    assert [c["label"] for c in cols] == ["A", "B"]
    assert rows == [["1", "2"], ["3", "4"]]

    html = "<table><tr><th>X</th><th>Y</th></tr><tr><td>a</td><td>b</td></tr></table>"
    cols, rows = tables.parse_html(html)
    assert [c["label"] for c in cols] == ["X", "Y"] and rows == [["a", "b"]]


def test_edit_policy_and_view_only():
    cols = tables.normalize_columns(["a", "b"])
    assert tables.normalize_edit(None, cols) is None
    assert tables.normalize_edit(False, cols) is None
    assert tables.normalize_edit(True, cols) == {
        "cells": True,
        "add_rows": False,
        "remove_rows": False,
        "add_columns": False,
        "remove_columns": False,
    }
    pol = tables.normalize_edit(
        {"cells": ["a"], "add_rows": True, "remove_rows": True}, cols
    )
    assert pol["cells"] == ["a"] and pol["add_rows"] and pol["remove_rows"]
    with pytest.raises(TableError, match="unknown column"):
        tables.normalize_edit({"cells": ["z"]}, cols)
    with pytest.raises(TableError, match="enables nothing"):
        tables.normalize_edit({"cells": False}, cols)


def test_build_state_sources_are_exclusive():
    state = tables.build_state(
        columns=["n"],
        rows=[["1"]],
        markdown=None,
        html_src=None,
        edit=None,
        submit_label="Done",
    )
    assert state["columns"][0]["id"] == "n" and state["edit"] is None
    with pytest.raises(TableError, match="alone"):
        tables.build_state(
            columns=["n"],
            rows=[["1"]],
            markdown="| n |\n|---|\n| 1 |",
            html_src=None,
            edit=None,
            submit_label="Done",
        )


def test_page_html_passes_artifact_check(tmp_path):
    html = tables.page_html("Scores")
    artifacts.check(html)
    assert "/static/js/table-artifact.js" in html
    path = tables.write_draft(tmp_path, "art-deadbeef", "Scores")
    assert path.read_text() == html


def test_too_many_rows_refused():
    with pytest.raises(TableError, match="2000"):
        tables.normalize_rows([["x"]] * 2001, tables.normalize_columns(["a"]))
