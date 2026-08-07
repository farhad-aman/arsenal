"""The clipboard watcher survives what the dashboard now survives.

It queues downloads and runs until stopped, so a refusal or an unwritable
folder has to be reported and stepped over rather than ending the session.
"""

import os
from collections import deque

import pytest

from dl import config as config_module
from dl import watch, ytjob
from dl.rpc import Aria2Error, Aria2Unreachable

URL = "https://e.com/a.iso"


class Client:
    def __init__(self, error=None):
        self.error = error
        self.added = []

    def tell_active(self):
        return []

    def tell_waiting(self):
        return []

    def add_uri(self, uris, options):
        if self.error:
            raise self.error
        self.added.append(uris)
        return "gid1"


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def locked_cfg(cfg, tmp_path):
    """A config whose every destination cannot be created."""
    blocked = tmp_path / "locked"
    blocked.mkdir(exist_ok=True)
    os.chmod(blocked, 0o500)
    cats = {
        name: config_module.Category(name, blocked / "sub", c.ext, c.icon, c.hue)
        for name, c in cfg.categories.items()
    }
    general = config_module.replace(cfg.general, default_dir=blocked / "sub")
    return config_module.Config(general, cfg.limits, cats, dict(cfg.domains)), blocked


def test_an_unwritable_destination_is_reported_not_raised(cfg, tmp_path, capsys):
    narrow, blocked = locked_cfg(cfg, tmp_path)
    try:
        assert watch.poll_once(URL, deque(), narrow, Client()) is False
    finally:
        os.chmod(blocked, 0o700)
    assert "cannot write" in capsys.readouterr().out.lower()


def test_an_unwritable_destination_queues_nothing(cfg, tmp_path):
    narrow, blocked = locked_cfg(cfg, tmp_path)
    client = Client()
    try:
        watch.poll_once(URL, deque(), narrow, client)
    finally:
        os.chmod(blocked, 0o700)
    assert client.added == []


def test_a_refused_add_is_reported_not_raised(cfg, capsys):
    client = Client(error=Aria2Error(1, "no such download"))
    assert watch.poll_once(URL, deque(), cfg, client) is False
    assert "no such download" in capsys.readouterr().out


def test_a_lost_daemon_does_not_end_the_watch(cfg, capsys):
    client = Client(error=Aria2Unreachable("connection refused"))
    assert watch.poll_once(URL, deque(), cfg, client) is False
    assert "connection refused" in capsys.readouterr().out


def test_a_good_url_still_gets_queued(cfg):
    client = Client()
    assert watch.poll_once(URL, deque(), cfg, client) is True
    assert client.added == [[URL]]


def test_the_watcher_keeps_going_after_a_refusal(cfg):
    """One bad URL must not stop the ones copied after it."""
    calls = {"n": 0}

    class Flaky(Client):
        def add_uri(self, uris, options):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Aria2Error(1, "refused")
            self.added.append(uris)
            return "gid"

    client = Flaky()
    seen: deque = deque(maxlen=20)
    watch.poll_once("https://e.com/one.iso", seen, cfg, client)
    watch.poll_once("https://e.com/two.iso", seen, cfg, client)
    assert client.added == [["https://e.com/two.iso"]]


def test_a_caught_youtube_link_honours_the_concurrency_cap(cfg, tmp_path, monkeypatch):
    """Ten copied links used to start ten supervisors at once."""
    from dl.tui import ytflow

    seen = {}

    def fake_spawn(job, state=None, cap=0):
        seen["cap"] = cap
        return True

    monkeypatch.setattr(ytflow, "spawn", fake_spawn)
    monkeypatch.setattr(ytjob, "ffmpeg_available", lambda: True)
    monkeypatch.setattr(watch.shutil, "which", lambda _name: "/usr/bin/yt-dlp")
    monkeypatch.setattr(
        watch.ytrun if hasattr(watch, "ytrun") else __import__("dl.ytrun", fromlist=["x"]),
        "probe",
        lambda job, timeout: ("A title", "", 0),
    )
    watch.poll_once("https://youtu.be/abc123", deque(), cfg, Client())
    assert seen.get("cap") == cfg.general.max_concurrent


async def test_the_dashboard_reports_an_unwritable_destination(sandbox_cfg, tmp_path, monkeypatch):
    """It used to die on the bare mkdir, while every other door checked first."""
    from dl.tui import app as app_module
    from dl.tui.app import DlApp
    from textual.widgets import Button, TextArea
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path / "state")
    narrow, blocked = locked_cfg(sandbox_cfg, tmp_path)
    notes = []
    app = DlApp(narrow, FakeClient())
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
            await pilot.press("a")
            await pilot.pause()
            app.screen.query_one("#urls", TextArea).text = "https://e.com/x.iso"
            app.screen.query_one("#ok", Button).press()
            await pilot.pause()
            await pilot.pause()
            assert app.is_running, "an unwritable folder brought the dashboard down"
    finally:
        os.chmod(blocked, 0o700)
    assert any("cannot write" in n for n in notes), notes
