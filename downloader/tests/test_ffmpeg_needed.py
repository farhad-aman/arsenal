"""Nearly every YouTube download needs ffmpeg, and used to find out last.

Without it yt-dlp fetches the streams, then fails at the postprocess step: the
audio-only download you asked for leaves a .webm behind, and the merge of a
separate video and audio track never happens.
"""

import pytest

from dl import ytjob
from dl.youtube import Choices


def choices(**over):
    base = dict(video="best", audio="best", subs="off", sub_lang="en", container="mp4")
    base.update(over)
    return Choices(**base)


def test_merging_a_separate_video_and_audio_needs_it():
    assert ytjob.needs_ffmpeg(choices()) is True


def test_extracting_audio_needs_it():
    assert ytjob.needs_ffmpeg(choices(video="none", container="flac")) is True


def test_embedding_subtitles_needs_it():
    assert ytjob.needs_ffmpeg(choices(subs="soft")) is True


def test_burning_subtitles_needs_it():
    assert ytjob.needs_ffmpeg(choices(subs="hard")) is True


def test_it_is_reported_present_when_the_binary_is_there(monkeypatch):
    monkeypatch.setattr(ytjob.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert ytjob.ffmpeg_available() is True


def test_it_is_reported_missing_when_the_binary_is_not(monkeypatch):
    monkeypatch.setattr(ytjob.shutil, "which", lambda name: None)
    assert ytjob.ffmpeg_available() is False


def test_burn_in_is_impossible_without_ffmpeg_at_all(monkeypatch):
    monkeypatch.setattr(ytjob.shutil, "which", lambda name: None)
    assert ytjob.burn_in_available() is False


def test_the_advice_names_the_thing_to_install():
    assert "ffmpeg" in ytjob.FFMPEG_ADVICE
    assert "brew" in ytjob.FFMPEG_ADVICE


async def test_the_youtube_flow_stops_before_fetching_anything(sandbox_cfg, monkeypatch, tmp_path):
    """It used to download the streams first and fail at the postprocess."""
    from dl.tui import app as app_module
    from dl.tui.app import DlApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ytjob, "ffmpeg_available", lambda: False)
    spawned = []
    monkeypatch.setattr(app_module.ytflow, "spawn", lambda *a, **k: spawned.append(a))
    notes = []

    app = DlApp(sandbox_cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        app._accept(["https://youtu.be/abc"])
        await pilot.pause()
        assert not any(
            type(s).__name__ == "YouTubeOptionsScreen" for s in app.screen_stack
        )
    assert spawned == []
    assert any("ffmpeg" in note for note in notes)


async def test_the_flow_runs_normally_when_ffmpeg_is_there(sandbox_cfg, monkeypatch, tmp_path):
    from dl.tui import app as app_module
    from dl.tui.app import DlApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    monkeypatch.setattr(ytjob, "ffmpeg_available", lambda: True)
    app = DlApp(sandbox_cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://youtu.be/abc"])
        await pilot.pause()
        assert type(app.screen).__name__ == "YouTubeOptionsScreen"


def test_the_clipboard_watcher_skips_a_youtube_link_without_ffmpeg(
    sandbox_cfg, monkeypatch, capsys
):
    """It would otherwise report the link as caught and quietly fail later."""
    from collections import deque

    from dl import watch

    monkeypatch.setattr(ytjob, "ffmpeg_available", lambda: False)
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda *a, **k: spawned.append(a))
    caught = watch.poll_once(
        "https://youtu.be/abc", deque(maxlen=5), sandbox_cfg, None
    )
    assert caught is False
    assert spawned == []
    assert "ffmpeg" in capsys.readouterr().out
