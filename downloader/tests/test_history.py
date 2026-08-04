import json

from dl import history


def test_tail_of_missing_file_is_empty(tmp_path):
    assert history.tail(tmp_path / "none.jsonl", 10) == []


def test_tail_of_empty_file_is_empty(tmp_path):
    p = tmp_path / "h.jsonl"
    p.touch()
    assert history.tail(p, 10) == []


def test_append_then_tail_roundtrips(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "a", "bytes": 1}, p)
    history.append({"name": "b", "bytes": 2}, p)
    assert [r["name"] for r in history.tail(p, 10)] == ["a", "b"]


def test_append_creates_parent_directories(tmp_path):
    p = tmp_path / "deep" / "deeper" / "h.jsonl"
    history.append({"name": "a"}, p)
    assert p.exists()


def test_tail_returns_only_last_n_oldest_first(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(50):
        history.append({"i": i}, p)
    got = history.tail(p, 5)
    assert [r["i"] for r in got] == [45, 46, 47, 48, 49]


def test_tail_skips_truncated_final_line(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"i": 1}, p)
    with open(p, "a") as fh:
        fh.write('{"i": 2, "name": "trunc')
    assert [r["i"] for r in history.tail(p, 10)] == [1]


def test_tail_skips_corrupt_middle_line(tmp_path):
    p = tmp_path / "h.jsonl"
    with open(p, "w") as fh:
        fh.write(json.dumps({"i": 1}) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"i": 3}) + "\n")
    assert [r["i"] for r in history.tail(p, 10)] == [1, 3]


def test_tail_handles_file_larger_than_one_block(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(2000):
        history.append({"i": i, "pad": "x" * 200}, p)
    got = history.tail(p, 3)
    assert [r["i"] for r in got] == [1997, 1998, 1999]


def test_records_are_one_line_each(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "has\nnewline"}, p)
    assert len(p.read_text().splitlines()) == 1


def test_tail_zero_returns_empty(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"i": 1}, p)
    assert history.tail(p, 0) == []
