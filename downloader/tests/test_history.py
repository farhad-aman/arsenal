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


def test_find_stops_once_it_has_enough(tmp_path, monkeypatch):
    """A search satisfied by recent downloads must not read the whole log.

    Reading forwards meant every keystroke in the search box parsed the entire
    history — 22ms on a 20k-line log, on every letter typed.
    """
    p = tmp_path / "h.jsonl"
    for i in range(5000):
        history.append({"name": f"old-{i}.bin"}, p)
    for i in range(3):
        history.append({"name": f"recent-{i}.iso"}, p)

    parsed = {"n": 0}
    real_loads = json.loads

    def counting(raw, *a, **k):
        parsed["n"] += 1
        return real_loads(raw, *a, **k)

    monkeypatch.setattr(history.json, "loads", counting)
    got = history.find(p, "recent", 3)
    assert [r["name"] for r in got] == ["recent-0.iso", "recent-1.iso", "recent-2.iso"]
    assert parsed["n"] < 100, f"parsed {parsed['n']} lines to find 3 at the end"


def test_find_still_reaches_the_start_when_it_has_to(tmp_path):
    """Early exit must not turn into 'only looks at the end'."""
    p = tmp_path / "h.jsonl"
    history.append({"name": "the-only-match.iso"}, p)
    for i in range(5000):
        history.append({"name": f"filler-{i}.bin"}, p)
    assert [r["name"] for r in history.find(p, "only-match", 10)] == ["the-only-match.iso"]


def test_find_handles_a_log_with_no_trailing_newline(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text(json.dumps({"name": "a.iso"}) + "\n" + json.dumps({"name": "b.iso"}))
    assert [r["name"] for r in history.find(p, "iso", 10)] == ["a.iso", "b.iso"]


def test_find_handles_a_record_larger_than_one_read_block(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "x" * 20000 + ".iso"}, p)
    history.append({"name": "small.iso"}, p)
    assert len(history.find(p, "iso", 10)) == 2


def test_find_survives_undecodable_bytes(tmp_path):
    p = tmp_path / "h.jsonl"
    with open(p, "wb") as fh:
        fh.write(b'{"name": "\xff\xfe bad.iso"}\n')
        fh.write(json.dumps({"name": "good.iso"}).encode() + b"\n")
    assert [r["name"] for r in history.find(p, "good", 10)] == ["good.iso"]
