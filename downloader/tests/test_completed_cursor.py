"""The completed tab holds its selection when the list underneath it changes.

Rows arrive newest-first, so every download that finishes pushes the whole list
down one. Holding the index moved the selection onto a different file — on the
tab that offers delete and re-download.
"""

import pytest

from dl import history, sort, theme
from dl.tui.completed import CompletedTable


def record(name: str, ts: int) -> dict:
    return {"ts": ts, "name": name, "bytes": 1, "path": "", "status": "ok", "category": "video"}


@pytest.fixture
def log(tmp_path):
    path = tmp_path / "history.jsonl"
    for i in range(3):
        history.append(record(f"old{i}.iso", 1000 + i), path)
    return path


def table(log) -> CompletedTable:
    widget = CompletedTable(theme.THEMES["aurora"])
    widget.load(log)
    return widget


def test_a_newly_finished_download_does_not_drag_the_selection(log):
    widget = table(log)
    widget.move(1)
    assert widget.selected["name"] == "old1.iso"
    history.append(record("JUST-FINISHED.iso", 2000), log)
    widget.load(log)
    assert widget.selected["name"] == "old1.iso"


def test_the_selection_shifts_down_by_one_index_to_stay_put(log):
    widget = table(log)
    widget.move(1)
    assert widget.cursor == 1
    history.append(record("JUST-FINISHED.iso", 2000), log)
    widget.load(log)
    assert widget.cursor == 2


def test_several_completions_in_a_row_still_hold_the_selection(log):
    widget = table(log)
    widget.move(2)
    assert widget.selected["name"] == "old0.iso"
    for i in range(4):
        history.append(record(f"new{i}.iso", 2000 + i), log)
        widget.load(log)
    assert widget.selected["name"] == "old0.iso"


def test_the_selection_survives_a_sort_change(log):
    widget = table(log)
    widget.move(2)
    picked = widget.selected["name"]
    widget.load(log, order=sort.Order("name", True))
    assert widget.selected["name"] == picked


def test_a_deleted_record_falls_back_to_the_slot(log):
    widget = table(log)
    widget.move(1)
    history.remove_entry(log, record("old1.iso", 1001))
    widget.load(log)
    assert widget.selected["name"] == "old0.iso"


def test_filtering_to_nothing_leaves_no_selection(log):
    widget = table(log)
    widget.move(1)
    widget.load(log, query="zzz-no-such-file")
    assert widget.selected is None
    assert widget.cursor == 0


def test_the_selection_returns_when_a_filter_is_cleared(log):
    widget = table(log)
    widget.move(1)
    picked = widget.selected["name"]
    widget.load(log, query="old1")
    assert widget.selected["name"] == picked
    widget.load(log)
    assert widget.selected["name"] == picked


def test_two_records_with_the_same_name_are_told_apart(log):
    """Identity is the whole record, not the filename: the same file downloaded
    twice is two rows, and the cursor must stay on the one that was picked."""
    history.append(record("same.iso", 3000), log)
    history.append(record("same.iso", 3001), log)
    widget = table(log)
    widget.load(log)
    widget.cursor = 1
    picked = widget.selected
    history.append(record("newest.iso", 4000), log)
    widget.load(log)
    assert widget.selected["ts"] == picked["ts"]


def test_an_empty_log_has_no_selection(tmp_path):
    widget = CompletedTable(theme.THEMES["aurora"])
    widget.load(tmp_path / "missing.jsonl")
    assert widget.selected is None
    assert widget.cursor == 0


def test_the_cursor_clamps_when_the_log_shrinks_past_it(log):
    widget = table(log)
    widget.move(2)
    for name, ts in (("old2.iso", 1002), ("old1.iso", 1001)):
        history.remove_entry(log, record(name, ts))
    widget.load(log)
    assert widget.selected["name"] == "old0.iso"
    assert widget.cursor == 0
