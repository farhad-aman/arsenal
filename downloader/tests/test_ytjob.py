import json

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


def test_command_runs_yt_dlp_through_aria2c(job):
    argv = ytjob.command(job)
    assert argv[0] == "yt-dlp"
    assert argv[argv.index("--downloader") + 1] == "aria2c"
    assert job["url"] == argv[-1]


def test_command_carries_the_chosen_options(tmp_path):
    picked = Choices("1080", "best", "soft", "fa", "mkv")
    job = ytjob.new_job("https://youtu.be/abc", tmp_path, picked)
    argv = ytjob.command(job)
    assert "height<=1080" in argv[argv.index("-f") + 1]
    assert argv[argv.index("--sub-langs") + 1] == "fa"
    assert argv[argv.index("--merge-output-format") + 1] == "mkv"


def test_command_writes_into_the_chosen_directory(tmp_path, job):
    argv = ytjob.command(job)
    template = argv[argv.index("-o") + 1]
    assert template.startswith(str(tmp_path / "dest"))
    assert "%(title)s" in template


def test_command_never_stops_on_the_first_error_of_a_playlist(job):
    assert "--ignore-errors" in ytjob.command(job)


def test_command_passes_the_proxy_when_the_job_wants_one(tmp_path):
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, proxy="http://127.0.0.1:2080")
    argv = ytjob.command(job)
    assert argv[argv.index("--proxy") + 1] == "http://127.0.0.1:2080"


def test_command_omits_the_proxy_by_default(job):
    assert "--proxy" not in ytjob.command(job)


def test_command_borrows_browser_cookies_when_configured(tmp_path):
    """YouTube refuses anonymous requests with 'confirm you're not a bot'."""
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, cookies_from="chrome")
    argv = ytjob.command(job)
    assert argv[argv.index("--cookies-from-browser") + 1] == "chrome"


def test_command_sends_no_cookies_when_disabled(tmp_path):
    job = ytjob.new_job("https://youtu.be/a", tmp_path, DEFAULTS, cookies_from="")
    assert "--cookies-from-browser" not in ytjob.command(job)


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


def test_finished_file_is_the_largest_media_file(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "clip.mp4").write_bytes(b"x" * 900)
    (dest / "clip.en.vtt").write_bytes(b"s" * 10)
    assert ytjob.final_file(dest).name == "clip.mp4"


def test_no_final_file_in_an_empty_directory(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    assert ytjob.final_file(dest) is None


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
