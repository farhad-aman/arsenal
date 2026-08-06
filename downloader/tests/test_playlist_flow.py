"""Pasting a playlist or channel URL, from expansion to spawned jobs."""

import pytest

from dl import playlist
from dl.tui import app as app_module
from dl.tui import ytadd
from dl.tui.app import DlApp
from tests.test_app import FakeClient

PLAYLIST = "https://www.youtube.com/playlist?list=PLxyz"


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def listing(monkeypatch):
    """A playlist of five, without asking YouTube."""
    entries = [
        playlist.Entry(f"https://youtu.be/v{i}", f"Episode {i}") for i in range(1, 6)
    ]
    monkeypatch.setattr(ytadd.playlist, "expand", lambda *a, **k: entries)
    return entries


@pytest.fixture
def spawned(monkeypatch):
    jobs = []
    monkeypatch.setattr(app_module.ytflow, "spawn", lambda job, state=None, cap=0: jobs.append(job))
    return jobs


async def open_playlist(app, pilot):
    app._accept([PLAYLIST])
    for _ in range(30):
        await pilot.pause()
        if any(type(s).__name__ == "PlaylistScreen" for s in app.screen_stack):
            return
    raise AssertionError("the playlist screen never opened")


async def test_a_playlist_url_asks_how_much_before_anything_else(cfg, listing, spawned):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        screen = app.screen
        assert screen.count == 5
        assert spawned == []


async def test_escape_at_the_count_queues_nothing(cfg, listing, spawned):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        await pilot.press("escape")
        await pilot.pause()
        assert spawned == []
        assert app.youtube_adder is None


async def test_accepting_asks_quality_once_then_spawns_one_job_per_video(
    cfg, listing, spawned
):
    """Five videos, one question. Asking five times is not a feature."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        await pilot.click("#all")
        await pilot.pause()
        assert type(app.screen).__name__ == "YouTubeOptionsScreen"
        await pilot.press("enter")
        await pilot.pause()
        assert type(app.screen).__name__ == "PickerScreen"
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if len(spawned) == 5:
                break
        assert len(spawned) == 5
        assert not any(
            type(s).__name__ in ("YouTubeOptionsScreen", "PickerScreen")
            for s in app.screen_stack
        )


async def test_every_job_carries_its_title_from_the_listing(cfg, listing, spawned):
    """The reason no probe is needed: the listing already said what these are."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        await pilot.click("#all")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if len(spawned) == 5:
                break
        assert [job["title"] for job in spawned] == [f"Episode {i}" for i in range(1, 6)]


async def test_every_job_shares_the_one_destination_and_quality(cfg, listing, spawned):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        await pilot.click("#all")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(30):
            await pilot.pause()
            if len(spawned) == 5:
                break
        assert len({job["dir"] for job in spawned}) == 1
        assert len({str(job["choices"]) for job in spawned}) == 1


async def test_newest_only_takes_the_front_of_the_list(cfg, monkeypatch, spawned):
    from dl import config as config_module

    entries = [
        playlist.Entry(f"https://youtu.be/v{i}", f"Episode {i}") for i in range(1, 41)
    ]
    monkeypatch.setattr(ytadd.playlist, "expand", lambda *a, **k: entries)
    app = DlApp(config_module.replace(cfg, newest=25), FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        assert app.screen.offers_newest is True
        await pilot.press("n")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if len(spawned) == 25:
                break
        assert len(spawned) == 25, "the configured limit, not a fixed 25"
        assert spawned[0]["title"] == "Episode 1"


async def test_a_short_playlist_is_not_offered_a_newest_option(cfg, listing, spawned):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        assert app.screen.offers_newest is False


async def test_a_listing_that_fails_says_so_and_queues_nothing(cfg, monkeypatch, spawned):
    def refuse(*a, **k):
        raise playlist.ListingFailed("private playlist")

    monkeypatch.setattr(ytadd.playlist, "expand", refuse)
    notes = []
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        app._accept([PLAYLIST])
        for _ in range(30):
            await pilot.pause()
            if notes:
                break
        assert spawned == []
        assert any("private playlist" in note for note in notes)


async def test_a_single_video_still_goes_through_the_probe(cfg, spawned, monkeypatch):
    """The collection path skips the probe; the single-video path must not."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://youtu.be/abc"])
        await pilot.pause()
        assert type(app.screen).__name__ == "YouTubeOptionsScreen"
        assert app.youtube_adder.shared is False


async def test_a_watch_url_inside_a_playlist_is_not_expanded(cfg, listing, spawned):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://www.youtube.com/watch?v=abc&list=PLxyz"])
        await pilot.pause()
        assert type(app.screen).__name__ == "YouTubeOptionsScreen"
        assert app.youtube_adder.shared is False


async def test_a_big_playlist_starts_only_as_many_as_the_cap_allows(
    cfg, monkeypatch, state
):
    """The point of the cap: accepting a collection used to start every video
    at the same moment, one supervisor process each."""
    from dl import config as config_module
    from dl import ytqueue

    entries = [
        playlist.Entry(f"https://youtu.be/v{i}", f"Episode {i}") for i in range(1, 21)
    ]
    monkeypatch.setattr(ytadd.playlist, "expand", lambda *a, **k: entries)
    spawned = []
    monkeypatch.setattr(ytqueue, "spawn", lambda job, st: spawned.append(job["id"]))

    capped = config_module.replace(
        cfg, general=config_module.replace(cfg.general, max_concurrent=3)
    )
    app = DlApp(capped, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        for _ in range(40):
            await pilot.pause()
            if len(app.table.rows) or spawned:
                break
        await pilot.pause()

    from dl import ytjob

    records = ytjob.list_jobs(state / "yt")
    assert len(records) == 20, "every video is on disk"
    assert len(spawned) == 3, f"only the cap runs, got {len(spawned)}"
    assert ytqueue.running(state / "yt") == 3


async def test_the_collection_limit_comes_from_the_config(cfg, monkeypatch, spawned):
    """It was a fixed 25 with no way to ask for more."""
    from dl import config as config_module

    entries = [
        playlist.Entry(f"https://youtu.be/v{i}", f"Episode {i}") for i in range(1, 300)
    ]
    monkeypatch.setattr(ytadd.playlist, "expand", lambda *a, **k: entries)
    app = DlApp(config_module.replace(cfg, newest=150), FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        assert app.screen.newest == 150
        assert "150" in app.screen.summary() or app.screen.offers_newest


async def test_a_collection_smaller_than_the_limit_offers_no_shortcut(cfg, listing, spawned):
    """Five videos with a limit of a hundred: "newest 100" would mean nothing."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
        assert app.screen.count == 5
        assert app.screen.offers_newest is False


async def test_expanding_waits_as_long_as_a_probe_would(cfg, monkeypatch, spawned):
    """A channel of thousands is one request, but a slow one. It used to give
    up after a fixed two minutes with no way to wait longer."""
    from dl import config as config_module

    seen = {}

    def record(url, proxy, cookies, limit=0, timeout=None):
        seen["timeout"] = timeout
        return [playlist.Entry("https://youtu.be/v1", "One")]

    monkeypatch.setattr(ytadd.playlist, "expand", record)
    app = DlApp(config_module.replace(cfg, probe_timeout=600), FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await open_playlist(app, pilot)
    assert seen["timeout"] == 600
