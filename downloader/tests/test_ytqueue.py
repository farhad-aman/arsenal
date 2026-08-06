"""Only so many yt-dlp jobs at once, and the one that finishes starts the next.

Accepting a 200-video playlist used to spawn 200 supervisors at the same
moment: max_concurrent governs aria2's queue, and nothing governed these.
"""

import time

import pytest

from dl import ytjob, ytqueue
from dl.youtube import DEFAULTS


@pytest.fixture
def state(tmp_path):
    (tmp_path / "yt").mkdir(parents=True)
    return tmp_path


def job(state, name="a", status="queued", started=0):
    made = ytjob.new_job(f"https://youtu.be/{name}", state / "out", DEFAULTS)
    made["id"] = f"yt-{name}"
    made["status"] = status
    made["started"] = started
    ytjob.save(state / "yt", made)
    return made


def test_nothing_is_running_to_begin_with(state):
    assert ytqueue.running(state / "yt") == 0


def test_taking_a_slot_counts_as_running(state):
    assert ytqueue.take_slot(state / "yt", "yt-a", cap=2) is True
    assert ytqueue.running(state / "yt") == 1


def test_slots_run_out_at_the_cap(state):
    assert ytqueue.take_slot(state / "yt", "yt-a", cap=2) is True
    assert ytqueue.take_slot(state / "yt", "yt-b", cap=2) is True
    assert ytqueue.take_slot(state / "yt", "yt-c", cap=2) is False
    assert ytqueue.running(state / "yt") == 2


def test_releasing_frees_a_slot(state):
    ytqueue.take_slot(state / "yt", "yt-a", cap=1)
    assert ytqueue.take_slot(state / "yt", "yt-b", cap=1) is False
    ytqueue.release(state / "yt", "yt-a")
    assert ytqueue.take_slot(state / "yt", "yt-b", cap=1) is True


def test_releasing_something_that_never_ran_is_quiet(state):
    ytqueue.release(state / "yt", "yt-nothing")


def test_a_cap_below_one_still_runs_one(state):
    """Zero would be a queue nothing ever leaves."""
    assert ytqueue.take_slot(state / "yt", "yt-a", cap=0) is True


def test_launch_starts_a_job_when_there_is_room(state, monkeypatch):
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    assert ytqueue.launch(job(state, "a"), state, cap=2) is True
    assert started == ["yt-a"]


def test_launch_leaves_a_job_queued_when_full(state, monkeypatch):
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    ytqueue.launch(job(state, "a"), state, cap=1)
    assert ytqueue.launch(job(state, "b"), state, cap=1) is False
    assert started == ["yt-a"]


def test_a_job_that_did_not_start_is_still_on_disk(state, monkeypatch):
    """It has to show in the dashboard, and something has to be able to find
    it later."""
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: None)
    ytqueue.launch(job(state, "a"), state, cap=1)
    ytqueue.launch(job(state, "b"), state, cap=1)
    ids = {j["id"] for j in ytjob.list_jobs(state / "yt")}
    assert ids == {"yt-a", "yt-b"}


def test_waiting_lists_queued_jobs_oldest_first(state):
    job(state, "b", started=200)
    job(state, "a", started=100)
    assert [j["id"] for j in ytqueue.waiting(state / "yt")] == ["yt-a", "yt-b"]


def test_waiting_skips_a_job_that_already_has_a_supervisor(state):
    job(state, "a", started=100)
    ytqueue.take_slot(state / "yt", "yt-a", cap=5)
    assert ytqueue.waiting(state / "yt") == []


def test_waiting_ignores_jobs_that_are_not_queued(state):
    job(state, "a", status="active")
    job(state, "b", status="error")
    job(state, "c", status="paused")
    assert ytqueue.waiting(state / "yt") == []


def test_finishing_starts_the_next_one(state, monkeypatch):
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    first = job(state, "a", started=1)
    ytqueue.launch(first, state, cap=1)
    ytqueue.launch(job(state, "b", started=2), state, cap=1)
    started.clear()

    # What a finishing supervisor leaves behind: a settled record, then the slot.
    ytjob.save(state / "yt", {**first, "status": "complete"})
    ytqueue.release(state / "yt", "yt-a")

    handed = ytqueue.start_next(state, cap=1)
    assert handed["id"] == "yt-b"
    assert started == ["yt-b"]


def test_a_job_whose_supervisor_vanished_is_picked_up_again(state, monkeypatch):
    """No status change and no claim means nothing is running it, whatever it
    once was. Leaving it queued forever would be worse."""
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    ytqueue.launch(job(state, "a", started=1), state, cap=1)
    started.clear()
    ytqueue.release(state / "yt", "yt-a")
    assert ytqueue.start_next(state, cap=1)["id"] == "yt-a"


def test_finishing_starts_nothing_when_the_queue_is_empty(state, monkeypatch):
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: None)
    assert ytqueue.start_next(state, cap=3) is None


def test_finishing_starts_nothing_while_the_cap_is_still_met(state, monkeypatch):
    """Two finishing together must not both hand on to the same slot."""
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: None)
    ytqueue.launch(job(state, "a", started=1), state, cap=1)
    job(state, "b", started=2)
    assert ytqueue.start_next(state, cap=1) is None


def test_the_same_queued_job_is_not_started_twice(state, monkeypatch):
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    job(state, "a", started=1)
    assert ytqueue.start_next(state, cap=4)["id"] == "yt-a"
    assert ytqueue.start_next(state, cap=4) is None
    assert started == ["yt-a"]


def test_a_stale_lock_does_not_wedge_the_queue(state):
    """A process killed mid-decision must not stop everything after it."""
    directory = state / "yt"
    stale = directory / ytqueue.LOCK
    stale.write_text("1")
    old = time.time() - ytqueue.STALE_LOCK - 1
    import os

    os.utime(stale, (old, old))
    assert ytqueue.take_slot(directory, "yt-a", cap=1) is True


def test_the_lock_is_not_left_behind(state):
    ytqueue.take_slot(state / "yt", "yt-a", cap=1)
    assert not (state / "yt" / ytqueue.LOCK).exists()
