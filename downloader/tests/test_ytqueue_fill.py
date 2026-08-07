"""Queued YouTube jobs get started even when no supervisor hands the slot on.

start_next only ever ran from a supervisor on its way out. A kill -9, a crash
or a reboot skips that, so the claims went stale, the slots came free, and the
jobs behind them sat queued for good.
"""

import os
import time

import pytest

from dl import youtube, ytjob, ytqueue


@pytest.fixture
def yard(tmp_path):
    directory = tmp_path / "yt"
    directory.mkdir(parents=True)
    return tmp_path


def queue_job(yard, url="https://youtu.be/x"):
    job = ytjob.new_job(url, yard / "dest", youtube.DEFAULTS)
    ytjob.save(yard / "yt", job)
    return job


def age_out(yard, job):
    """A supervisor that died without releasing its claim."""
    claim = yard / "yt" / f"{job['id']}{ytqueue.CLAIM}"
    old = time.time() - ytqueue.HANDOVER - 5
    os.utime(claim, (old, old))


@pytest.fixture
def spawned(monkeypatch):
    seen = []
    monkeypatch.setattr(ytqueue, "spawn", lambda job, state: seen.append(job["id"]))
    return seen


def test_free_slots_counts_what_is_left(yard):
    assert ytqueue.free_slots(yard / "yt", 3) == 3
    ytqueue.hold_slot(yard / "yt", "yt-a")
    assert ytqueue.free_slots(yard / "yt", 3) == 2


def test_free_slots_never_goes_negative(yard):
    for i in range(4):
        ytqueue.hold_slot(yard / "yt", f"yt-{i}")
    assert ytqueue.free_slots(yard / "yt", 2) == 0


def test_fill_starts_a_queued_job_when_nothing_holds_a_slot(yard, spawned):
    job = queue_job(yard)
    assert [j["id"] for j in ytqueue.fill(yard, 2)] == [job["id"]]
    assert spawned == [job["id"]]


def test_fill_starts_every_job_a_dead_supervisor_left_behind(yard, spawned):
    """The reboot case: three queued, two claims gone stale, cap 2."""
    jobs = [queue_job(yard, f"https://youtu.be/v{i}") for i in range(3)]
    for job in jobs[:2]:
        ytqueue.hold_slot(yard / "yt", job["id"])
        age_out(yard, job)
    started = ytqueue.fill(yard, 2)
    assert len(started) == 2, "both free slots should have been taken"
    assert len(spawned) == 2


def test_fill_respects_the_cap(yard, spawned):
    for i in range(5):
        queue_job(yard, f"https://youtu.be/v{i}")
    assert len(ytqueue.fill(yard, 2)) == 2
    assert len(spawned) == 2


def test_fill_starts_nothing_when_the_slots_are_busy(yard, spawned):
    queue_job(yard)
    ytqueue.hold_slot(yard / "yt", "yt-busy")
    assert ytqueue.fill(yard, 1) == []
    assert spawned == []


def test_fill_starts_nothing_when_nothing_is_queued(yard, spawned):
    assert ytqueue.fill(yard, 3) == []
    assert spawned == []


def test_fill_leaves_a_job_that_already_has_a_claim(yard, spawned):
    job = queue_job(yard)
    ytqueue.hold_slot(yard / "yt", job["id"])
    assert ytqueue.fill(yard, 3) == []
    assert spawned == []


def test_fill_ignores_jobs_that_are_not_queued(yard, spawned):
    job = queue_job(yard)
    job["status"] = "paused"
    ytjob.save(yard / "yt", job)
    assert ytqueue.fill(yard, 3) == []


def test_fill_takes_the_oldest_first(yard, spawned):
    first = queue_job(yard, "https://youtu.be/old")
    first["started"] = 1000
    ytjob.save(yard / "yt", first)
    second = queue_job(yard, "https://youtu.be/new")
    second["started"] = 2000
    ytjob.save(yard / "yt", second)
    assert [j["id"] for j in ytqueue.fill(yard, 1)] == [first["id"]]


def test_fill_on_a_missing_directory_is_quiet(tmp_path, spawned):
    assert ytqueue.fill(tmp_path / "nothing-here", 2) == []


def test_a_departing_supervisor_reclaims_every_free_slot(yard, spawned):
    """hand_over used to start exactly one, however many slots had come free."""
    from dl import ytrun

    leaving = queue_job(yard, "https://youtu.be/leaving")
    ytqueue.hold_slot(yard / "yt", leaving["id"])
    dead = queue_job(yard, "https://youtu.be/dead")
    ytqueue.hold_slot(yard / "yt", dead["id"])
    age_out(yard, dead)
    for i in range(2):
        queue_job(yard, f"https://youtu.be/waiting{i}")

    ytrun.hand_over(yard / "yt", leaving["id"], 3)
    assert len(spawned) == 3, f"expected all three free slots filled, got {spawned}"


async def test_the_dashboard_starts_a_stranded_queue_when_it_opens(
    sandbox_cfg, tmp_path, monkeypatch, spawned
):
    """The reboot case, end to end: open dl and the queue moves again."""
    from dl.tui import app as app_module
    from dl.tui.app import DlApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    (tmp_path / "yt").mkdir(parents=True)
    job = ytjob.new_job("https://youtu.be/stranded", tmp_path / "dest", youtube.DEFAULTS)
    ytjob.save(tmp_path / "yt", job)

    app = DlApp(sandbox_cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
    assert spawned == [job["id"]]


async def test_the_preview_does_not_start_other_peoples_downloads(
    sandbox_cfg, tmp_path, monkeypatch, spawned
):
    from dl.tui import app as app_module
    from dl.tui.preview import PreviewApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    (tmp_path / "yt").mkdir(parents=True)
    ytjob.save(
        tmp_path / "yt",
        ytjob.new_job("https://youtu.be/someone-elses", tmp_path / "dest", youtube.DEFAULTS),
    )

    app = PreviewApp(sandbox_cfg, FakeClient(), gids=["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
    assert spawned == []
