import pytest

from dl import history
from dl.tui import app as app_module
from dl.tui.app import DlApp
from tests.test_app import FakeClient


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def copied(monkeypatch):
    """Never touch the real clipboard from a test run."""
    seen = []
    monkeypatch.setattr(app_module, "write_clipboard", lambda text: seen.append(text) or True)
    return seen


def record(**over):
    base = {
        "ts": 1,
        "name": "ubuntu.iso",
        "bytes": 10,
        "path": "/tmp/ubuntu.iso",
        "category": "iso",
        "url": "https://e.com/ubuntu.iso",
        "status": "ok",
    }
    base.update(over)
    return base


def log_with(state, *records):
    path = state / "history.jsonl"
    for entry in records:
        history.append(entry, path)
    return path


async def on_completed(app, pilot):
    await pilot.pause()
    await pilot.press("tab")
    await pilot.pause()


async def test_y_copies_the_source_url_of_a_finished_download(cfg, state, copied):
    log_with(state, record())
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("y")
        await pilot.pause()
    assert copied == ["https://e.com/ubuntu.iso"]


async def test_capital_y_copies_the_file_path(cfg, state, copied):
    log_with(state, record())
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("Y")
        await pilot.pause()
    assert copied == ["/tmp/ubuntu.iso"]


async def test_y_copies_the_url_on_the_active_tab_too(cfg, copied):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("y")
        await pilot.pause()
    assert copied == ["https://e.com/a.iso"]


async def test_copying_a_record_without_a_url_says_so(cfg, state, copied):
    log_with(state, record(url=""))
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("y")
        await pilot.pause()
    assert copied == []


async def test_a_clipboard_that_refuses_is_reported(cfg, state, monkeypatch):
    log_with(state, record())
    monkeypatch.setattr(app_module, "write_clipboard", lambda text: False)
    notes = []
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        monkeypatch.setattr(
            app, "notify", lambda msg, **kw: notes.append((msg, kw.get("severity")))
        )
        await on_completed(app, pilot)
        await pilot.press("y")
        await pilot.pause()
    assert notes and notes[-1][1] == "error"


async def test_r_queues_a_finished_download_again(cfg, state):
    log_with(state, record())
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("r")
        await pilot.pause()
    assert client.added == ["https://e.com/ubuntu.iso"]


async def test_re_downloading_goes_through_routing_and_the_proxy_rules(cfg, state):
    """Not a bare add_uri: the destination, proxy and headers are decided the
    same way a fresh download's would be."""
    from dl import config as config_module

    routed = config_module.replace(cfg, proxy_domains=("e.com",))
    log_with(state, record())
    client = FakeClient()
    app = DlApp(routed, client)
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("r")
        await pilot.pause()
    assert client.add_calls
    options = client.add_calls[0][1]
    assert options.get("all-proxy") == routed.proxy
    assert options.get("dir")


async def test_r_on_a_record_without_a_url_says_so(cfg, state):
    log_with(state, record(url=""))
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("r")
        await pilot.pause()
    assert client.added == []


async def test_r_on_a_youtube_record_opens_the_quality_picker(cfg, state):
    """aria2 handed a watch page fetches the HTML, so this must not reach it."""
    log_with(state, record(name="clip.mp4", url="https://youtu.be/abc"))
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("r")
        await pilot.pause()
        assert client.added == []
        assert any(
            type(screen).__name__ == "YouTubeOptionsScreen" for screen in app.screen_stack
        )


async def test_adding_a_youtube_url_opens_the_quality_picker(cfg, state):
    """The same gap by the other door: pasting a watch URL into `a`."""
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://youtu.be/abc"])
        await pilot.pause()
        assert client.added == []
        assert any(
            type(screen).__name__ == "YouTubeOptionsScreen" for screen in app.screen_stack
        )


async def test_a_mixed_batch_splits_between_aria2_and_yt_dlp(cfg, state):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://e.com/fresh.iso", "https://youtu.be/abc"])
        await pilot.pause()
        assert client.added == ["https://e.com/fresh.iso"]
        assert any(
            type(screen).__name__ == "YouTubeOptionsScreen" for screen in app.screen_stack
        )


async def test_the_quality_picker_waits_for_a_duplicate_question_to_be_answered(cfg, state):
    """Both push screens. Asking at once puts the picker on top of a question
    about a different download."""
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        # a.iso is already in flight on the FakeClient, so this collides.
        app._accept(["https://e.com/a.iso", "https://youtu.be/abc"])
        await pilot.pause()
        assert type(app.screen).__name__ == "DuplicateModal"
        assert app.youtube_adder is None
        await pilot.press("escape")
        await pilot.pause()
        assert any(
            type(screen).__name__ == "YouTubeOptionsScreen" for screen in app.screen_stack
        )


async def test_a_second_youtube_batch_is_refused_while_one_is_open(cfg, state):
    """Two adders pushing screens at once would interleave their questions."""
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._accept(["https://youtu.be/abc"])
        await pilot.pause()
        first = app.youtube_adder
        app._accept(["https://youtu.be/def"])
        await pilot.pause()
        assert app.youtube_adder is first


async def test_deleting_from_completed_keeps_the_filter(cfg, state):
    """The reload used to drop the query, so the list jumped back to the full
    log the moment an entry was removed."""
    log_with(state, record(name="ubuntu.iso"), record(name="debian.iso"), record(name="clip.mp4"))
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await on_completed(app, pilot)
        await pilot.press("slash")
        await pilot.pause()
        for ch in "iso":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.completed.rows) == 2
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.search_query == "iso"
        assert all("iso" in row["name"] for row in app.completed.rows), app.completed.rows
