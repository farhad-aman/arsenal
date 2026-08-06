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


def stale_claim(directory, job_id, pid="999999"):
    """As a reboot leaves them: written long ago, by a process now gone."""
    import os

    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{job_id}{ytqueue.CLAIM}"
    path.write_text(pid)
    old = time.time() - ytqueue.HANDOVER - 1
    os.utime(path, (old, old))
    return path


def test_a_claim_left_by_a_dead_process_does_not_hold_a_slot(state):
    """kill -9, a crash or a reboot skips the release. Counting files rather
    than live processes meant those slots were occupied for good: three of
    them at max_concurrent 3 and YouTube downloads stopped for ever."""
    directory = state / "yt"
    stale_claim(directory, "yt-dead")
    assert ytqueue.running(directory) == 0
    assert ytqueue.take_slot(directory, "yt-new", cap=1) is True


def test_a_claim_being_kept_fresh_still_counts(state):
    directory = state / "yt"
    directory.mkdir(parents=True, exist_ok=True)
    ytqueue.hold_slot(directory, "yt-live")
    assert ytqueue.running(directory) == 1
    assert ytqueue.take_slot(directory, "yt-new", cap=1) is False


def test_touching_keeps_a_claim_from_going_stale(state):
    directory = state / "yt"
    stale = stale_claim(directory, "yt-a")
    assert ytqueue.running(directory) == 0
    ytqueue.hold_slot(directory, "yt-a")
    ytqueue.touch(directory, "yt-a")
    assert ytqueue.running(directory) == 1


def test_touching_a_claim_that_is_gone_puts_it_back(state):
    """The sweep may have taken it while the supervisor was between polls."""
    directory = state / "yt"
    directory.mkdir(parents=True, exist_ok=True)
    ytqueue.touch(directory, "yt-a")
    assert ytqueue.running(directory) == 1


def test_a_stale_claim_is_cleared_away_rather_than_left(state):
    directory = state / "yt"
    directory.mkdir(parents=True, exist_ok=True)
    stale = stale_claim(directory, "yt-dead")
    ytqueue.claims(directory)
    assert not stale.exists()


def test_a_claim_with_nothing_readable_in_it_is_not_trusted(state):
    """A file written half way through, or emptied by a crash."""
    directory = state / "yt"
    directory.mkdir(parents=True, exist_ok=True)
    stale_claim(directory, "yt-odd", pid="")
    assert ytqueue.running(directory) == 0


def test_a_queued_job_becomes_startable_again_once_its_claim_goes_stale(state, monkeypatch):
    """The whole point: work resumes by itself after a reboot."""
    started = []
    monkeypatch.setattr(ytqueue, "spawn", lambda j, s: started.append(j["id"]))
    job(state, "a", started=1)
    stale_claim(state / "yt", "yt-a")
    assert ytqueue.start_next(state, cap=1)["id"] == "yt-a"
    assert started == ["yt-a"]


def test_a_freshly_written_claim_is_trusted(state):
    """A `dl <url>` that exits straight after starting something must not
    leave the claim looking dead while its supervisor is still starting up."""
    directory = state / "yt"
    directory.mkdir(parents=True, exist_ok=True)
    ytqueue.hold_slot(directory, "yt-fresh")
    assert ytqueue.running(directory) == 1


def test_a_claim_nothing_is_touching_stops_being_trusted(state):
    stale_claim(state / "yt", "yt-old")
    assert ytqueue.running(state / "yt") == 0


def test_a_claim_is_trusted_for_a_while_after_it_is_taken(state):
    """Between taking a slot and the supervisor's first poll, nothing is
    touching it yet, and that gap must not free the slot."""
    directory = state / "yt"
    ytqueue.take_slot(directory, "yt-a", cap=1)
    assert ytqueue.running(directory) == 1
