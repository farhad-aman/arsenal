import json
import time

from dl import started


def test_an_unrecorded_gid_has_no_time(tmp_path):
    assert started.when(tmp_path, "abc") == 0


def test_recording_then_reading_back(tmp_path):
    started.record(tmp_path, "abc", 1000)
    assert started.when(tmp_path, "abc") == 1000


def test_record_defaults_to_now(tmp_path):
    before = int(time.time())
    started.record(tmp_path, "abc")
    assert before <= started.when(tmp_path, "abc") <= int(time.time())


def test_load_returns_every_recorded_gid(tmp_path):
    started.record(tmp_path, "a", 1)
    started.record(tmp_path, "b", 2)
    assert started.load(tmp_path) == {"a": 1, "b": 2}


def test_load_on_a_missing_file_is_empty(tmp_path):
    assert started.load(tmp_path) == {}


def test_the_first_recording_wins(tmp_path):
    """A gid is created once. A later line for it is a retry writing the same
    download, and the original queue time is the honest one."""
    started.record(tmp_path, "a", 1000)
    started.record(tmp_path, "a", 2000)
    assert started.when(tmp_path, "a") == 1000


def test_a_corrupt_line_does_not_lose_the_rest(tmp_path):
    started.record(tmp_path, "a", 1)
    (tmp_path / started.LOG).open("a").write("{not json\n")
    started.record(tmp_path, "b", 2)
    assert started.load(tmp_path) == {"a": 1, "b": 2}


def test_a_line_without_a_gid_is_ignored(tmp_path):
    (tmp_path / started.LOG).write_text('{"ts": 5}\n')
    assert started.load(tmp_path) == {}


def test_recording_creates_the_state_directory(tmp_path):
    nested = tmp_path / "deep" / "state"
    started.record(nested, "a", 1)
    assert started.when(nested, "a") == 1


def test_prune_keeps_live_gids_and_drops_the_rest(tmp_path):
    for gid in ("a", "b", "c"):
        started.record(tmp_path, gid, 1)
    started.prune(tmp_path, ["a", "c"])
    assert set(started.load(tmp_path)) == {"a", "c"}


def test_prune_preserves_the_recorded_times(tmp_path):
    started.record(tmp_path, "a", 111)
    started.record(tmp_path, "b", 222)
    started.prune(tmp_path, ["b"])
    assert started.when(tmp_path, "b") == 222


def test_prune_leaves_a_valid_log_to_append_to(tmp_path):
    started.record(tmp_path, "a", 1)
    started.record(tmp_path, "b", 2)
    started.prune(tmp_path, ["a"])
    started.record(tmp_path, "c", 3)
    assert started.load(tmp_path) == {"a": 1, "c": 3}
    for line in (tmp_path / started.LOG).read_text().splitlines():
        json.loads(line)


def test_prune_on_a_missing_file_is_quiet(tmp_path):
    started.prune(tmp_path, ["a"])
    assert started.load(tmp_path) == {}


def test_prune_to_nothing_empties_the_log(tmp_path):
    started.record(tmp_path, "a", 1)
    started.prune(tmp_path, [])
    assert started.load(tmp_path) == {}


def test_overgrown_is_false_below_the_limit(tmp_path):
    started.record(tmp_path, "a", 1)
    assert started.overgrown(started.load(tmp_path)) is False


def test_overgrown_is_true_above_the_limit(tmp_path):
    assert started.overgrown({str(i): i for i in range(started.LIMIT + 1)}) is True


def test_the_client_records_the_time_a_download_was_added(tmp_path, monkeypatch):
    """The plumbing, not just the store: every add_uri caller depends on this."""
    from dl.rpc import Aria2

    client = Aria2("127.0.0.1", 1, "secret", state=tmp_path)
    monkeypatch.setattr(Aria2, "_call", lambda self, method, *params: "gid42")
    assert client.add_uri(["http://e.com/a.iso"], {}) == "gid42"
    assert started.when(tmp_path, "gid42") > 0


def test_a_client_with_no_state_records_nothing(tmp_path, monkeypatch):
    from dl.rpc import Aria2

    client = Aria2("127.0.0.1", 1, "secret")
    monkeypatch.setattr(Aria2, "_call", lambda self, method, *params: "gid42")
    client.add_uri(["http://e.com/a.iso"], {})
    assert started.load(tmp_path) == {}


def test_an_unwritable_state_dir_does_not_break_the_add(tmp_path, monkeypatch):
    """A lost timestamp costs a column; a raised error costs the download."""
    from dl.rpc import Aria2

    monkeypatch.setattr(started, "record", _boom)
    client = Aria2("127.0.0.1", 1, "secret", state=tmp_path)
    monkeypatch.setattr(Aria2, "_call", lambda self, method, *params: "gid42")
    assert client.add_uri(["http://e.com/a.iso"], {}) == "gid42"


def _boom(*args, **kwargs):
    raise OSError("read-only file system")
