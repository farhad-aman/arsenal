"""aria2 refusing a call must not take the dashboard down.

A download that finishes between the poll and the keypress is gone by the time
aria2 is asked about it. Every key that reaches the daemon has to survive that.
"""

import pytest
from textual.widgets import Input

from dl import config as config_module
from dl.rpc import Aria2Error, Aria2Unreachable
from dl.tui.app import DlApp
from tests.test_app import FakeClient

GONE = Aria2Error(1, "GID g1 is not found")
LOST = Aria2Unreachable("connection refused")


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def refusing(error, *methods) -> FakeClient:
    client = FakeClient()

    def boom(*args, **kwargs):
        raise error

    for name in methods:
        setattr(client, name, boom)
    return client


async def survives(cfg, client, key, tmp_path, monkeypatch) -> DlApp:
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert app.is_running, f"pressing {key} brought the dashboard down"
    return app


async def test_space_survives_a_refused_pause(cfg, tmp_path, monkeypatch):
    await survives(cfg, refusing(GONE, "pause", "unpause"), "space", tmp_path, monkeypatch)


async def test_space_survives_a_lost_daemon(cfg, tmp_path, monkeypatch):
    await survives(cfg, refusing(LOST, "pause", "unpause"), "space", tmp_path, monkeypatch)


async def test_pause_all_survives_a_refusal(cfg, tmp_path, monkeypatch):
    await survives(cfg, refusing(GONE, "pause"), "p", tmp_path, monkeypatch)


async def test_resume_all_survives_a_refusal(cfg, tmp_path, monkeypatch):
    await survives(cfg, refusing(GONE, "unpause"), "u", tmp_path, monkeypatch)


async def test_reorder_survives_a_refusal(cfg, tmp_path, monkeypatch):
    await survives(cfg, refusing(GONE, "change_position"), "J", tmp_path, monkeypatch)


async def test_pause_all_reports_once_however_many_rows_refuse(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    notes = []
    app = DlApp(cfg, refusing(GONE, "pause"))
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        assert len(app.table.rows) > 1
        await pilot.press("p")
        await pilot.pause()
    assert len(notes) == 1, f"one message per press, not per row: {notes}"


async def test_a_working_pause_all_says_nothing(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    notes = []
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        await pilot.press("p")
        await pilot.pause()
    assert notes == []


async def test_pause_all_keeps_going_after_one_row_refuses(cfg, tmp_path, monkeypatch):
    """A refusal on the first row must not strand the rest of the queue."""
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    client = FakeClient()
    asked = []

    def sometimes(gid):
        asked.append(gid)
        if gid == "g1":
            raise GONE

    client.pause = sometimes
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        await pilot.pause()
    assert asked == ["g1", "g2"]


async def test_the_limit_key_survives_a_refused_change(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    app = DlApp(cfg, refusing(GONE, "change_option"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        app.screen.query_one("#rate", Input).value = "500K"
        await pilot.press("enter")
        await pilot.pause()
        assert app.is_running


async def test_a_refused_limit_does_not_claim_it_worked(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    notes = []
    app = DlApp(cfg, refusing(GONE, "change_option"))
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        await pilot.press("l")
        await pilot.pause()
        app.screen.query_one("#rate", Input).value = "500K"
        await pilot.press("enter")
        await pilot.pause()
    assert not any("limit 500K" in note for note in notes), notes


async def test_a_working_limit_still_confirms(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    notes = []
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        monkeypatch.setattr(app, "notify", lambda msg, **kw: notes.append(msg))
        await pilot.press("l")
        await pilot.pause()
        app.screen.query_one("#rate", Input).value = "500K"
        await pilot.press("enter")
        await pilot.pause()
    assert any("limit 500K" in note for note in notes), notes


async def test_adding_a_url_survives_a_refused_add(cfg, tmp_path, monkeypatch):
    from textual.widgets import Button, TextArea

    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    app = DlApp(cfg, refusing(GONE, "add_uri"))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        app.screen.query_one("#urls", TextArea).text = "https://example.com/x.iso"
        app.screen.query_one("#ok", Button).press()
        await pilot.pause()
        await pilot.pause()
        assert app.is_running
