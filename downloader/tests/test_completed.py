import json
import time

import pytest

from dl import history, theme
from dl.tui.completed import CompletedTable, record_path, render_entry


@pytest.fixture
def th():
    return theme.THEMES["aurora"]


def record(**over):
    base = {
        "ts": int(time.time()) - 120,
        "name": "movie.mkv",
        "bytes": 455774430,
        "path": "/tmp/movie.mkv",
        "category": "video",
        "status": "ok",
    }
    base.update(over)
    return base


def test_record_path_returns_none_for_empty():
    assert record_path(record(path="")) is None
    assert record_path(record()).name == "movie.mkv"


def test_render_entry_shows_name_size_category_and_time(th):
    now = int(time.time())
    line = render_entry(record(), th, selected=False, now=now)
    assert "movie.mkv" in line
    assert "435 MB" in line
    assert "video" in line
    assert time.strftime("%H:%M", time.localtime(now - 120)) in line


def test_render_entry_never_says_ago(th):
    assert "ago" not in render_entry(record(), th, selected=False, now=int(time.time()))


def test_render_entry_dates_an_older_download(th):
    then = int(time.time()) - 40 * 86400
    line = render_entry(record(ts=then), th, selected=False, now=int(time.time()))
    assert time.strftime("%Y-%m-%d %H:%M", time.localtime(then)) in line


def test_render_entry_shows_a_dash_when_the_record_has_no_timestamp(th):
    from dl.format import DASH

    assert DASH in render_entry(record(ts=0), th, selected=False, now=int(time.time()))


def test_render_entry_marks_selection(th):
    now = int(time.time())
    assert "▌" in render_entry(record(), th, selected=True, now=now)
    assert "▌" not in render_entry(record(), th, selected=False, now=now)


def test_render_entry_flags_a_missing_file(th, tmp_path):
    line = render_entry(record(path=str(tmp_path / "gone.mkv")), th, False, int(time.time()))
    assert "file gone" in line


def test_render_entry_does_not_flag_an_existing_file(th, tmp_path):
    real = tmp_path / "here.mkv"
    real.write_text("x")
    line = render_entry(record(path=str(real)), th, False, int(time.time()))
    assert "file gone" not in line


def test_render_entry_uses_cross_for_errors(th):
    assert "❌" in render_entry(record(status="error"), th, False, int(time.time()))


def test_render_entry_mono_has_no_markup():
    line = render_entry(record(), theme.THEMES["mono"], True, int(time.time()))
    assert "[#" not in line


def test_table_loads_newest_first(tmp_path):
    log = tmp_path / "history.jsonl"
    for i in range(3):
        history.append(record(name=f"f{i}.mkv", ts=1000 + i), log)
    table = CompletedTable(theme.THEMES["aurora"])
    table.load(log)
    assert [r["name"] for r in table.rows] == ["f2.mkv", "f1.mkv", "f0.mkv"]


def test_table_selection_moves_and_clamps(tmp_path):
    log = tmp_path / "history.jsonl"
    for i in range(3):
        history.append(record(name=f"f{i}.mkv", ts=1000 + i), log)
    table = CompletedTable(theme.THEMES["aurora"])
    table.load(log)
    assert table.selected["name"] == "f2.mkv"
    table.move(1)
    assert table.selected["name"] == "f1.mkv"
    table.move(-5)
    assert table.selected["name"] == "f2.mkv"
    table.move(99)
    assert table.selected["name"] == "f0.mkv"


def test_table_with_no_history_has_no_selection(tmp_path):
    table = CompletedTable(theme.THEMES["aurora"])
    table.load(tmp_path / "missing.jsonl")
    assert table.selected is None
    assert table.rows == []


def test_remove_entry_drops_only_the_matching_record(tmp_path):
    log = tmp_path / "history.jsonl"
    a, b, c = record(name="a", ts=1), record(name="b", ts=2), record(name="c", ts=3)
    for r in (a, b, c):
        history.append(r, log)
    assert history.remove_entry(log, b) is True
    assert [r["name"] for r in history.tail(log, 10)] == ["a", "c"]


def test_remove_entry_returns_false_when_absent(tmp_path):
    log = tmp_path / "history.jsonl"
    history.append(record(name="a", ts=1), log)
    assert history.remove_entry(log, record(name="zz", ts=99)) is False
    assert len(history.tail(log, 10)) == 1


def test_remove_entry_on_missing_file_is_false(tmp_path):
    assert history.remove_entry(tmp_path / "nope.jsonl", record()) is False


def test_remove_entry_removes_only_one_of_two_identical_records(tmp_path):
    log = tmp_path / "history.jsonl"
    dupe = record(name="same", ts=5)
    history.append(dupe, log)
    history.append(dupe, log)
    assert history.remove_entry(log, dupe) is True
    assert len(history.tail(log, 10)) == 1


def test_remove_entry_keeps_the_file_valid_jsonl(tmp_path):
    log = tmp_path / "history.jsonl"
    for i in range(4):
        history.append(record(name=f"f{i}", ts=i), log)
    history.remove_entry(log, record(name="f2", ts=2))
    for line in log.read_text().splitlines():
        json.loads(line)


def test_a_proxied_record_is_badged(th):
    from dl.tui.completed import render_entry

    record = {"name": "a.iso", "status": "ok", "bytes": 10, "ts": 1, "path": "", "proxy": True}
    assert "🌐" in render_entry(record, th, selected=False, now=2)


def test_a_direct_record_is_not_badged(th):
    from dl.tui.completed import render_entry

    record = {"name": "a.iso", "status": "ok", "bytes": 10, "ts": 1, "path": ""}
    assert "🌐" not in render_entry(record, th, selected=False, now=2)
