from collections import deque
from dataclasses import asdict

import pytest

from dl import ytrun
from dl.youtube import DEFAULTS

DEFAULTS_DICT = asdict(DEFAULTS)


def test_rate_needs_two_samples_before_it_claims_anything():
    samples = deque()
    assert ytrun.rate(samples, 0, now=0.0) == 0


def test_rate_measures_across_the_window_not_one_poll():
    """One poll against the last reads zero whenever a poll lands between
    aria2's flushes, which is most of them."""
    samples = deque()
    ytrun.rate(samples, 0, now=0.0)
    ytrun.rate(samples, 0, now=0.5)          # flush has not landed yet
    got = ytrun.rate(samples, 3_000_000, now=1.0)
    assert got == 3_000_000


def test_rate_forgets_samples_older_than_the_window():
    samples = deque()
    ytrun.rate(samples, 0, now=0.0)
    ytrun.rate(samples, 1_000_000, now=10.0)
    ytrun.rate(samples, 2_000_000, now=11.0)
    assert samples[0][0] >= 10.0 - ytrun.WINDOW


def test_rate_of_a_stalled_download_decays_to_zero():
    samples = deque()
    ytrun.rate(samples, 5_000_000, now=0.0)
    for tick in range(1, 12):
        got = ytrun.rate(samples, 5_000_000, now=float(tick))
    assert got == 0


def test_rate_never_goes_negative_if_bytes_vanish():
    samples = deque()
    ytrun.rate(samples, 9_000_000, now=0.0)
    assert ytrun.rate(samples, 1_000, now=1.0) == 0


def test_reported_speed_reads_aria2s_own_rate(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("[#853350 13MiB/14MiB(91%) CN:3 DL:1.4MiB]\n")
    assert ytrun.reported_speed(log) == int(1.4 * 1024 * 1024)


def test_reported_speed_takes_the_most_recent_line(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        "[#1 1MiB/14MiB(7%) CN:3 DL:9.0MiB]\n"
        "[#1 8MiB/14MiB(57%) CN:3 DL:2.5MiB]\n"
    )
    assert ytrun.reported_speed(log) == int(2.5 * 1024 * 1024)


def test_reported_speed_handles_kilobytes(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("[#1 1MiB/14MiB(7%) CN:1 DL:512KiB]\n")
    assert ytrun.reported_speed(log) == 512 * 1024


def test_reported_speed_is_minus_one_when_aria2_said_nothing(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("[youtube] Extracting URL\n")
    assert ytrun.reported_speed(log) == -1


def test_reported_speed_of_a_missing_log_is_minus_one(tmp_path):
    assert ytrun.reported_speed(tmp_path / "nope.log") == -1


def test_last_error_prefers_the_error_line(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        "[youtube] Extracting URL: https://youtu.be/x\n"
        "[youtube] x: Downloading webpage\n"
        "ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies\n"
    )
    assert "Sign in to confirm" in ytrun.last_error(log)
    assert not ytrun.last_error(log).startswith("ERROR:")


def test_last_error_falls_back_to_the_final_line(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("something went sideways\n")
    assert ytrun.last_error(log) == "something went sideways"


def test_last_error_of_a_missing_log_is_empty(tmp_path):
    assert ytrun.last_error(tmp_path / "nope.log") == ""


def test_last_error_of_an_empty_log_is_empty(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("")
    assert ytrun.last_error(log) == ""


def test_last_error_is_capped(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("ERROR: " + "x" * 5000)
    assert len(ytrun.last_error(log)) <= 300


def test_the_supervisor_stands_down_when_the_job_is_paused():
    """Pausing must not be recorded as a failure — the dashboard resumes from
    the record, and an errored job would offer retry instead."""
    assert ytrun.stand_down("paused") is True


def test_the_supervisor_stands_down_when_the_job_is_cancelled():
    assert ytrun.stand_down("cancelled") is True


def test_the_supervisor_keeps_going_while_the_job_is_active():
    assert ytrun.stand_down("active") is False


def test_a_probe_that_times_out_is_not_reported_as_a_blank_answer(monkeypatch):
    """Empty strings read as 'nothing to check', which silently skips the
    duplicate prompt and leaves the row with no title, size or progress."""
    import subprocess

    def die(*a, **k):
        raise subprocess.TimeoutExpired("yt-dlp", 60)

    monkeypatch.setattr(ytrun.subprocess, "run", die)
    with pytest.raises(ytrun.ProbeFailed):
        ytrun.probe({"url": "https://youtu.be/x", "choices": DEFAULTS_DICT, "dir": "/tmp"})


def test_a_probe_that_cannot_start_also_raises(monkeypatch):
    def die(*a, **k):
        raise OSError("yt-dlp not runnable")

    monkeypatch.setattr(ytrun.subprocess, "run", die)
    with pytest.raises(ytrun.ProbeFailed):
        ytrun.probe({"url": "https://youtu.be/x", "choices": DEFAULTS_DICT, "dir": "/tmp"})


def test_the_probe_timeout_is_configurable(monkeypatch):
    """Resolving a filtered link through a proxy ranges from 20s to minutes, so
    the cap has to be tunable rather than baked in at 60."""
    seen = {}

    class Done:
        stdout = "clip\n/tmp/clip.mp4\n99\n"

    monkeypatch.setattr(
        ytrun.subprocess, "run", lambda *a, **k: seen.update(k) or Done()
    )
    ytrun.probe(
        {"url": "https://youtu.be/x", "choices": DEFAULTS_DICT, "dir": "/tmp"}, timeout=300
    )
    assert seen["timeout"] == 300


def test_the_default_probe_timeout_survives_a_slow_proxy():
    assert ytrun.PROBE_TIMEOUT >= 180
