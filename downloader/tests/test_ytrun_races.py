"""What happens when the job record moves under a running supervisor.

The record is the only channel between the dashboard and the supervisor, and
the dashboard deletes it. Reading it unguarded meant a delete either crashed
the supervisor mid-download or, worse, let finalize write the record back.
"""

import json

import pytest

from dl import config, youtube, ytjob, ytqueue, ytrun


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No desktop notification and no user hook: finalize would otherwise shell
    out to osascript, which blocks the whole run."""
    monkeypatch.setattr(ytrun, "notify", lambda title, body: True)
    monkeypatch.setattr(ytrun, "after_complete", lambda cfg, record, root: "")


@pytest.fixture
def cfg():
    base = config.defaults()
    return config.replace(base, general=config.replace(base.general, notify=False))


@pytest.fixture
def yard(tmp_path):
    (tmp_path / "yt").mkdir(parents=True)
    return tmp_path / "yt"


@pytest.fixture
def job(yard, tmp_path):
    made = ytjob.new_job("https://youtu.be/x", tmp_path / "dest", youtube.DEFAULTS)
    ytjob.save(yard, made)
    return made


class FakeProc:
    """yt-dlp that keeps running until someone terminates it.

    Bounded so a regression that stops terminating fails the test instead of
    spinning the suite forever.
    """

    LIMIT = 50

    def __init__(self, pid=4242):
        self.pid = pid
        self.returncode = None
        self.terminated = False
        self.polls = 0

    def poll(self):
        self.polls += 1
        if self.polls > self.LIMIT and self.returncode is None:
            raise AssertionError("supervisor never let go of yt-dlp")
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15


@pytest.fixture
def running(monkeypatch):
    proc = FakeProc()
    monkeypatch.setattr(ytrun.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ytrun, "probe", lambda job, timeout: ("A title", "", 0))
    monkeypatch.setattr(ytrun, "POLL", 0.0)
    monkeypatch.setattr(ytjob, "bytes_on_disk", lambda d: 0)
    monkeypatch.setattr(ytrun, "reported_speed", lambda log, tail=4000: -1)
    return proc


def test_deleting_the_record_stops_yt_dlp(yard, job, running, monkeypatch, cfg):
    """Delete means stop. The supervisor used to raise instead, leaving the
    yt-dlp process and its aria2 children behind."""
    seen = {"polls": 0}
    real_read = ytjob.read

    def read_then_delete(path):
        seen["polls"] += 1
        if seen["polls"] == 1:
            (yard / f"{job['id']}.json").unlink()
        return real_read(path)

    monkeypatch.setattr(ytjob, "read", read_then_delete)
    ytrun._run(yard, dict(job), cfg)
    assert running.terminated, "yt-dlp was left running"


def test_deleting_the_record_does_not_bring_it_back(yard, job, running, monkeypatch, cfg):
    """finalize writes the record on its way out, which recreated what the
    dashboard had just deleted — as an error row that could not be removed."""
    real_read = ytjob.read
    polls = {"n": 0}

    def read_then_delete(path):
        polls["n"] += 1
        if polls["n"] == 1:
            (yard / f"{job['id']}.json").unlink()
        return real_read(path)

    monkeypatch.setattr(ytjob, "read", read_then_delete)
    ytrun._run(yard, dict(job), cfg)
    assert not (yard / f"{job['id']}.json").exists(), "the deleted record came back"


def test_a_corrupt_record_is_treated_as_gone(yard, job, running, monkeypatch, cfg):
    """Corrupted mid-download — half a write, a full disk. Unreadable is as
    good as deleted: there is no instruction left to follow."""

    def unreadable(path):
        raise json.JSONDecodeError("bad", "{", 0)

    monkeypatch.setattr(ytjob, "read", unreadable)
    ytrun._run(yard, dict(job), cfg)
    assert running.terminated


def test_a_normal_finish_still_writes_the_record(yard, job, monkeypatch, tmp_path, cfg):
    proc = FakeProc()
    proc.returncode = 0
    monkeypatch.setattr(ytrun.subprocess, "Popen", lambda *a, **k: proc)
    monkeypatch.setattr(ytrun, "probe", lambda job, timeout: ("A title", "", 0))
    monkeypatch.setattr(ytrun, "POLL", 0.0)
    ytrun._run(yard, dict(job), cfg)
    saved = json.loads((yard / f"{job['id']}.json").read_text())
    assert saved["status"] == "complete"


def test_a_pause_still_stands_down_without_erroring(yard, job, running, monkeypatch, cfg):
    real_read = ytjob.read
    polls = {"n": 0}

    def read_then_pause(path):
        polls["n"] += 1
        record = real_read(path)
        if polls["n"] == 1:
            record["status"] = "paused"
            ytjob.save(yard, record)
        return record

    monkeypatch.setattr(ytjob, "read", read_then_pause)
    assert ytrun._run(yard, dict(job), cfg) == 0
    assert json.loads((yard / f"{job['id']}.json").read_text())["status"] == "paused"


def test_bytes_on_disk_survives_a_file_vanishing(tmp_path, monkeypatch):
    scratch = tmp_path / "part"
    scratch.mkdir()
    (scratch / "a.bin").write_bytes(b"x" * 10)
    gone = scratch / "b.bin"
    gone.write_bytes(b"y" * 10)

    real_stat = type(gone).stat

    def flaky(self, *a, **k):
        if self.name == "b.bin":
            raise FileNotFoundError(self)
        return real_stat(self, *a, **k)

    monkeypatch.setattr(type(gone), "stat", flaky)
    assert ytjob.bytes_on_disk(scratch) == 10


def test_sweep_survives_a_record_vanishing(tmp_path, monkeypatch):
    directory = tmp_path / "yt"
    directory.mkdir(parents=True)
    made = ytjob.new_job("https://youtu.be/x", tmp_path / "dest", youtube.DEFAULTS)
    ytjob.save(directory, made)
    real_list = ytjob.list_jobs

    def list_then_delete(where):
        jobs = real_list(where)
        (directory / f"{made['id']}.json").unlink(missing_ok=True)
        return jobs

    monkeypatch.setattr(ytjob, "list_jobs", list_then_delete)
    ytjob.sweep(directory, None)


def test_take_slot_refuses_rather_than_racing_without_the_lock(tmp_path, monkeypatch):
    """Failing to lock used to fall through and claim anyway, so two
    supervisors could both take the last slot."""
    directory = tmp_path / "yt"
    directory.mkdir(parents=True)
    # Slots to spare, so only the missing lock can refuse this.
    monkeypatch.setattr(ytqueue, "_lock", lambda d: None)
    assert ytqueue.take_slot(directory, "yt-new", 3) is False
    assert not (directory / "yt-new.claim").exists()


def test_take_slot_still_works_when_the_lock_is_available(tmp_path):
    directory = tmp_path / "yt"
    directory.mkdir(parents=True)
    assert ytqueue.take_slot(directory, "yt-new", 3) is True
    assert (directory / "yt-new.claim").exists()
