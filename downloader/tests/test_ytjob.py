import json
import os
import subprocess
import sys
import time

import pytest

from dl import ytjob
from dl.youtube import Choices, DEFAULTS


@pytest.fixture
def job(tmp_path):
    return ytjob.new_job("https://youtu.be/abc", tmp_path / "dest", DEFAULTS)


def test_new_job_has_an_id_and_starts_queued(job):
    assert job["id"].startswith("yt-")
    assert job["status"] == "queued"
    assert job["done"] == 0
    assert job["url"] == "https://youtu.be/abc"


def test_ids_do_not_collide(tmp_path):
    made = {ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS)["id"] for _ in range(50)}
    assert len(made) == 50


def test_job_round_trips_through_disk(tmp_path, job):
    path = ytjob.save(tmp_path, job)
    assert ytjob.read(path)["id"] == job["id"]


def test_save_is_atomic_enough_to_never_leave_a_partial_file(tmp_path, job):
    path = ytjob.save(tmp_path, job)
    for _ in range(20):
        ytjob.save(tmp_path, job)
        json.loads(path.read_text())


def test_listing_returns_every_saved_job(tmp_path):
    for i in range(3):
        ytjob.save(tmp_path, ytjob.new_job(f"https://youtu.be/{i}", tmp_path, DEFAULTS))
    assert len(ytjob.list_jobs(tmp_path)) == 3


def test_listing_an_empty_or_missing_directory_is_fine(tmp_path):
    assert ytjob.list_jobs(tmp_path / "nope") == []


def test_listing_skips_a_corrupt_job_file(tmp_path):
    ytjob.save(tmp_path, ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS))
    (tmp_path / "yt-broken.json").write_text("{not json")
    assert len(ytjob.list_jobs(tmp_path)) == 1


def test_command_runs_yt_dlp_through_aria2c(job, tmp_path):
    argv = ytjob.command(job, tmp_path)
    assert argv[0] == "yt-dlp"
    assert argv[argv.index("--downloader") + 1] == "aria2c"
    assert job["url"] == argv[-1]


def test_command_carries_the_chosen_options(tmp_path):
    picked = Choices("1080", "best", "soft", "fa", "mkv")
    job = ytjob.new_job("https://youtu.be/abc", tmp_path, picked)
    argv = ytjob.command(job, tmp_path)
    assert "height<=1080" in argv[argv.index("-f") + 1]
    assert argv[argv.index("--sub-langs") + 1] == "fa"
    assert argv[argv.index("--merge-output-format") + 1] == "mkv"


def test_command_sends_the_finished_file_to_the_chosen_directory(tmp_path, job):
    argv = ytjob.command(job, tmp_path)
    assert f"home:{tmp_path / 'dest'}" in argv
    assert "%(title)s" in argv[argv.index("-o") + 1]


def test_command_keeps_fragments_out_of_the_destination(tmp_path, job):
    """Two jobs sharing a folder would otherwise see each other's leftovers."""
    argv = ytjob.command(job, tmp_path)
    scratch = f"temp:{ytjob.scratch_dir(tmp_path, job)}"
    assert scratch in argv
    assert str(tmp_path / "dest") not in scratch


def test_command_asks_yt_dlp_to_report_the_file_it_produced(tmp_path, job):
    argv = ytjob.command(job, tmp_path)
    assert argv[argv.index("--print-to-file") + 1] == "after_move:filepath"
    assert str(ytjob.result_marker(tmp_path, job)) in argv


def test_command_never_stops_on_the_first_error_of_a_playlist(job, tmp_path):
    assert "--ignore-errors" in ytjob.command(job, tmp_path)


def test_command_passes_the_proxy_when_the_job_wants_one(tmp_path):
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, proxy="http://127.0.0.1:2080")
    argv = ytjob.command(job, tmp_path)
    assert argv[argv.index("--proxy") + 1] == "http://127.0.0.1:2080"


def test_command_omits_the_proxy_by_default(job, tmp_path):
    assert "--proxy" not in ytjob.command(job, tmp_path)


def test_command_borrows_browser_cookies_when_configured(tmp_path):
    """YouTube refuses anonymous requests with 'confirm you're not a bot'."""
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, cookies_from="chrome")
    argv = ytjob.command(job, tmp_path)
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome"


def test_command_sends_no_cookies_when_disabled(tmp_path):
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, cookies_from="")
    assert "--cookies-from-browser" not in ytjob.command(job, tmp_path)


def test_progress_sums_the_partial_files(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "clip.f137.mp4.part").write_bytes(b"x" * 100)
    (dest / "clip.f140.m4a.part").write_bytes(b"y" * 50)
    assert ytjob.bytes_on_disk(dest) == 150


def test_progress_counts_finished_pieces_too(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "clip.mp4").write_bytes(b"x" * 400)
    assert ytjob.bytes_on_disk(dest) == 400


def test_progress_ignores_aria2_control_files(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "clip.mp4.part").write_bytes(b"x" * 100)
    (dest / "clip.mp4.part.aria2").write_bytes(b"c" * 9999)
    assert ytjob.bytes_on_disk(dest) == 100


def test_progress_of_a_missing_directory_is_zero(tmp_path):
    assert ytjob.bytes_on_disk(tmp_path / "gone") == 0


def test_probe_only_simulates(job):
    argv = ytjob.probe_command(job)
    assert "--simulate" in argv
    assert argv[0] == "yt-dlp"
    assert argv[-1] == job["url"]


def test_probe_asks_for_the_title_and_the_size(job):
    argv = ytjob.probe_command(job)
    printed = [argv[i + 1] for i, a in enumerate(argv) if a == "--print"]
    assert printed == ["%(title)s", "%(filename)s", "%(filesize,filesize_approx)s"]


def test_probe_measures_the_format_that_will_be_fetched(tmp_path):
    picked = Choices("480", "best", "off", "en", "mp4")
    job = ytjob.new_job("https://youtu.be/a", tmp_path, picked)
    argv = ytjob.probe_command(job)
    assert "height<=480" in argv[argv.index("-f") + 1]


def test_probe_carries_the_proxy_and_cookies(tmp_path):
    job = ytjob.new_job(
        "https://youtu.be/a", tmp_path, DEFAULTS, proxy="http://p:1", cookies_from="chrome"
    )
    argv = ytjob.probe_command(job)
    assert argv[argv.index("--proxy") + 1] == "http://p:1"
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome"


def test_parse_probe_reads_the_title_path_and_total():
    got = ytjob.parse_probe("Some Video\n/movies/Some Video.mp4\n18206810\n")
    assert got == ("Some Video", "/movies/Some Video.mp4", 18206810)


def test_parse_probe_survives_an_unknown_size():
    """yt-dlp prints NA when it cannot work the size out."""
    assert ytjob.parse_probe("Some Video\n/m/x.mp4\nNA\n") == ("Some Video", "/m/x.mp4", 0)


def test_parse_probe_of_nothing_is_empty():
    assert ytjob.parse_probe("") == ("", "", 0)


def test_probe_asks_for_the_path_yt_dlp_would_write(job):
    """Titles need sanitising before they are filenames; yt-dlp does that."""
    argv = ytjob.probe_command(job)
    printed = [argv[i + 1] for i, a in enumerate(argv) if a == "--print"]
    assert "%(filename)s" in printed


def test_command_uses_an_explicit_output_name_when_one_was_chosen(tmp_path, job):
    job["outname"] = "clip (2).mp4"
    argv = ytjob.command(job, tmp_path)
    assert argv[argv.index("-o") + 1] == "clip (2).mp4"


def test_command_forces_overwriting_only_when_asked(tmp_path, job):
    assert "--force-overwrites" not in ytjob.command(job, tmp_path)
    job["force"] = True
    assert "--force-overwrites" in ytjob.command(job, tmp_path)


def test_produced_file_comes_from_what_yt_dlp_reported(tmp_path, job):
    landed = tmp_path / "clip.mp4"
    landed.write_bytes(b"x" * 900)
    ytjob.result_marker(tmp_path, job).write_text(f"{landed}\n")
    assert ytjob.produced_file(tmp_path, job) == landed


def test_produced_file_ignores_a_path_that_is_not_there(tmp_path, job):
    ytjob.result_marker(tmp_path, job).write_text(f"{tmp_path / 'gone.mp4'}\n")
    assert ytjob.produced_file(tmp_path, job) is None


def test_produced_file_without_a_marker_is_none(tmp_path, job):
    assert ytjob.produced_file(tmp_path, job) is None


def test_produced_file_takes_the_last_line_for_a_playlist(tmp_path, job):
    first, second = tmp_path / "a.mp4", tmp_path / "b.mp4"
    for f in (first, second):
        f.write_bytes(b"x")
    ytjob.result_marker(tmp_path, job).write_text(f"{first}\n{second}\n")
    assert ytjob.produced_file(tmp_path, job) == second


def test_clean_scratch_removes_the_workspace_and_marker(tmp_path, job):
    scratch = ytjob.scratch_dir(tmp_path, job)
    scratch.mkdir(parents=True)
    (scratch / "frag.part").write_bytes(b"x")
    ytjob.result_marker(tmp_path, job).write_text("x")
    ytjob.clean_scratch(tmp_path, job)
    assert not scratch.exists()
    assert not ytjob.result_marker(tmp_path, job).exists()


def test_burn_command_targets_the_subtitle_and_writes_beside_it(tmp_path):
    video = tmp_path / "clip.mp4"
    subs = tmp_path / "clip.en.vtt"
    argv, out = ytjob.burn_command(video, subs)
    assert argv[0] == "ffmpeg"
    assert f"subtitles={subs.name}" in argv
    assert out != video
    assert out.suffix == video.suffix


def test_burn_command_uses_bare_names_so_the_filtergraph_stays_simple(tmp_path):
    """A full path drags ':' and '\\' into the filtergraph, which need escaping."""
    argv, out = ytjob.burn_command(tmp_path / "clip.mp4", tmp_path / "clip.en.vtt")
    assert str(tmp_path) not in " ".join(argv)
    assert argv[argv.index("-i") + 1] == "clip.mp4"


def test_burn_in_availability_is_probed_not_assumed(monkeypatch):
    monkeypatch.setattr(ytjob.shutil, "which", lambda _n: None)
    assert ytjob.burn_in_available() is False


def test_burn_in_available_when_ffmpeg_lists_the_filter(monkeypatch):
    class Done:
        stdout = " T. subtitles         V->V       Render text subtitles onto input video.\n"

    monkeypatch.setattr(ytjob.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ytjob.subprocess, "run", lambda *a, **k: Done())
    assert ytjob.burn_in_available() is True


def test_burn_in_unavailable_when_the_filter_is_absent(monkeypatch):
    class Done:
        stdout = " .. anull             A->A       Pass the source unchanged.\n"

    monkeypatch.setattr(ytjob.shutil, "which", lambda _n: "/usr/bin/ffmpeg")
    monkeypatch.setattr(ytjob.subprocess, "run", lambda *a, **k: Done())
    assert ytjob.burn_in_available() is False


def test_a_listed_domain_gives_a_youtube_job_its_proxy():
    from dl import config, routing

    cfg = config.replace(config.defaults(), proxy_domains=("youtube.com",))
    assert routing.through_proxy("https://www.youtube.com/watch?v=x", cfg) is True


def test_a_job_built_without_a_proxy_passes_none_to_yt_dlp(tmp_path):
    from dl.youtube import DEFAULTS

    job = ytjob.new_job("https://youtu.be/x", tmp_path, DEFAULTS, proxy="")
    assert "--proxy" not in ytjob.command(job, tmp_path)
    assert "--proxy" not in ytjob.probe_command(job)


def live_job(tmp_path, **over):
    from dl.youtube import DEFAULTS

    job = ytjob.new_job("https://youtu.be/abc", tmp_path / "out", DEFAULTS)
    job.update({"status": "active", "supervisor": os.getpid(), **over})
    return job


def dead_pid():
    """A pid that has certainly exited."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_a_job_whose_supervisor_is_alive_is_not_orphaned(tmp_path):
    assert ytjob.orphaned(live_job(tmp_path)) is False


def test_a_job_whose_supervisor_died_is_orphaned(tmp_path):
    assert ytjob.orphaned(live_job(tmp_path, supervisor=dead_pid())) is True


def test_a_job_that_has_not_started_its_supervisor_yet_is_left_alone(tmp_path):
    """Between spawn and the supervisor's first write there is no pid to check,
    and a probe can hold that state for minutes."""
    assert ytjob.orphaned(live_job(tmp_path, status="queued", supervisor=0)) is False


def test_a_finished_job_is_never_orphaned(tmp_path):
    assert ytjob.orphaned(live_job(tmp_path, status="complete", supervisor=dead_pid())) is False


def test_reaping_records_why_the_row_stopped(tmp_path):
    job = ytjob.save(tmp_path, live_job(tmp_path, supervisor=dead_pid()))
    reaped = ytjob.reap(tmp_path, ytjob.read(job))
    assert reaped["status"] == "error"
    assert reaped["error"]
    assert ytjob.read(job)["status"] == "error", "must persist, not just report"


def test_sweeping_reaps_an_orphan(tmp_path):
    ytjob.save(tmp_path, live_job(tmp_path, supervisor=dead_pid()))
    ytjob.sweep(tmp_path)
    assert [j["status"] for j in ytjob.list_jobs(tmp_path)] == ["error"]


def logged(tmp_path, job):
    from dl import history

    log = tmp_path / "history.jsonl"
    history.append({"ts": 1, "name": "x", "url": job["url"], "status": "ok"}, log)
    return log


def test_sweeping_drops_a_finished_record_once_history_has_it(tmp_path):
    job = live_job(tmp_path, status="complete")
    saved = ytjob.save(tmp_path, job)
    saved.with_suffix(".log").write_text("noise")
    log = logged(tmp_path, job)
    ytjob.sweep(tmp_path, log, now=time.time() + ytjob.KEEP_FINISHED + 1)
    assert ytjob.list_jobs(tmp_path) == []
    assert not saved.with_suffix(".log").exists()


def test_sweeping_keeps_a_finished_record_history_never_received(tmp_path):
    """These records are the fallback for exactly that failure. Dropping one on
    age alone would erase the only trace the download ever happened."""
    job = live_job(tmp_path, status="complete")
    ytjob.save(tmp_path, job)
    ytjob.sweep(tmp_path, tmp_path / "history.jsonl", now=time.time() + ytjob.KEEP_FINISHED + 1)
    assert len(ytjob.list_jobs(tmp_path)) == 1


def test_sweeping_keeps_a_finished_record_that_is_still_fresh(tmp_path):
    """The watch view polls its own ids until they settle; pruning one out from
    under it would leave it waiting on a record that no longer exists."""
    job = live_job(tmp_path, status="complete")
    ytjob.save(tmp_path, job)
    ytjob.sweep(tmp_path, logged(tmp_path, job))
    assert len(ytjob.list_jobs(tmp_path)) == 1


def test_sweeping_without_a_history_log_never_drops_a_record(tmp_path):
    job = live_job(tmp_path, status="complete")
    ytjob.save(tmp_path, job)
    ytjob.sweep(tmp_path, now=time.time() + ytjob.KEEP_FINISHED + 1)
    assert len(ytjob.list_jobs(tmp_path)) == 1


def test_sweeping_removes_fragments_left_by_a_job_that_is_gone(tmp_path):
    orphan = tmp_path / "yt-gone.part"
    orphan.mkdir(parents=True)
    (orphan / "frag").write_bytes(b"x" * 1024)
    (tmp_path / "yt-gone.final").write_text("/tmp/x.mp4")
    ytjob.sweep(tmp_path)
    assert not orphan.exists()
    assert not (tmp_path / "yt-gone.final").exists()


def test_sweeping_leaves_fragments_belonging_to_a_live_job(tmp_path):
    job = live_job(tmp_path)
    ytjob.save(tmp_path, job)
    scratch = ytjob.scratch_dir(tmp_path, job)
    scratch.mkdir(parents=True)
    ytjob.sweep(tmp_path)
    assert scratch.exists()
