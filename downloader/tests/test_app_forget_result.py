"""Deleting from the dashboard must not leave the download in the stopped list.

aria2.remove moves it there rather than erasing it, and `dl ls` was showing
every past deletion for the life of the daemon.
"""

import pytest

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


class Purging(FakeClient):
    def __init__(self):
        super().__init__()
        self.purged = []

    def remove_download_result(self, gid):
        self.purged.append(gid)
        return "OK"


async def test_deleting_from_the_list_forgets_the_result(cfg):
    client = Purging()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#list")
        await pilot.pause()
    assert client.removed == ["g1"]
    assert client.purged == ["g1"]


async def test_a_daemon_without_the_call_does_not_break_deleting(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("d")
        await pilot.pause()
        await pilot.click("#list")
        await pilot.pause()
    assert client.removed == ["g1"]
