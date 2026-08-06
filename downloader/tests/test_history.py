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


def test_find_reaches_past_the_records_tail_would_return(tmp_path):
    """The Completed tab shows the last 200. A search that stopped there would
    quietly fail to find anything older, which is worse than no search."""
    p = tmp_path / "h.jsonl"
    history.append({"name": "ubuntu-ancient.iso"}, p)
    for i in range(500):
        history.append({"name": f"filler-{i}.bin"}, p)
    assert [r["name"] for r in history.find(p, "ancient", 200)] == ["ubuntu-ancient.iso"]


def test_find_of_missing_file_is_empty(tmp_path):
    assert history.find(tmp_path / "none.jsonl", "x", 10) == []


def test_find_returns_oldest_first(tmp_path):
    p = tmp_path / "h.jsonl"
    for name in ("a-iso", "b-iso", "c-iso"):
        history.append({"name": name}, p)
    assert [r["name"] for r in history.find(p, "iso", 10)] == ["a-iso", "b-iso", "c-iso"]


def test_find_keeps_the_newest_when_more_match_than_the_limit(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(10):
        history.append({"name": f"clip-{i}.mp4"}, p)
    got = history.find(p, "clip", 3)
    assert [r["name"] for r in got] == ["clip-7.mp4", "clip-8.mp4", "clip-9.mp4"]


def test_find_with_an_empty_query_returns_the_tail(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(5):
        history.append({"name": f"n{i}"}, p)
    assert [r["name"] for r in history.find(p, "", 2)] == ["n3", "n4"]


def test_find_skips_corrupt_lines(tmp_path):
    p = tmp_path / "h.jsonl"
    with open(p, "w") as fh:
        fh.write(json.dumps({"name": "one.iso"}) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"name": "two.iso"}) + "\n")
    assert [r["name"] for r in history.find(p, "iso", 10)] == ["one.iso", "two.iso"]


def test_find_matches_a_name_case_insensitively(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "Ubuntu-24.04.ISO"}, p)
    assert len(history.find(p, "ubuntu", 10)) == 1


def test_find_ignores_records_without_a_name(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"bytes": 1}, p)
    history.append({"name": "ubuntu.iso"}, p)
    assert [r["name"] for r in history.find(p, "ubuntu", 10)] == ["ubuntu.iso"]
