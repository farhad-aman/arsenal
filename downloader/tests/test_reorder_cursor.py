"""J and K move a download through the queue, and the cursor goes with it."""

from pathlib import Path

import pytest

from dl import config, theme
from dl.routing import OTHER
from dl.rpc import Aria2Error, Aria2Unreachable
from dl.tui.app import DlApp
from dl.tui.table import DownloadTable, Row
from tests.test_app import FakeClient


@pytest.fixture
def cfg():
    return config.defaults()


def row(gid: str) -> Row:
    return Row(
        gid=gid,
        name=f"{gid}.iso",
        status="waiting",
        total=100,
        done=0,
        speed=0,
        eta=-1,
        category=OTHER,
        path=Path(f"/tmp/{gid}.iso"),
        conns=0,
        error="",
    )


def table(*gids: str) -> DownloadTable:
    widget = DownloadTable(theme.THEMES["aurora"])
    widget.set_rows([row(g) for g in gids])
    return widget


def test_the_cursor_starts_on_the_first_row():
    assert table("a", "b", "c").selected_gid == "a"


def test_the_cursor_follows_its_download_when_the_queue_reorders():
    widget = table("a", "b", "c")
    widget.move(1)
    assert widget.selected_gid == "b"
    widget.set_rows([row(g) for g in ("b", "a", "c")])
    assert widget.selected_gid == "b"
    assert widget.cursor == 0


def test_moving_up_repeatedly_walks_the_same_download_to_the_front():
    """The bug: the cursor kept the index, so K swapped two rows back and forth."""
    widget = table("a", "b", "c", "d")
    widget.move(3)
    assert widget.selected_gid == "d"
    for order in (("a", "b", "d", "c"), ("a", "d", "b", "c"), ("d", "a", "b", "c")):
        widget.set_rows([row(g) for g in order])
        assert widget.selected_gid == "d"
    assert widget.cursor == 0


def test_moving_down_repeatedly_walks_the_same_download_to_the_back():
    widget = table("a", "b", "c", "d")
    assert widget.selected_gid == "a"
    for order in (("b", "a", "c", "d"), ("b", "c", "a", "d"), ("b", "c", "d", "a")):
        widget.set_rows([row(g) for g in order])
        assert widget.selected_gid == "a"
    assert widget.cursor == 3


def test_a_finished_download_above_the_cursor_does_not_drag_the_selection():
    widget = table("a", "b", "c")
    widget.move(2)
    assert widget.selected_gid == "c"
    widget.set_rows([row(g) for g in ("b", "c")])
    assert widget.selected_gid == "c"


def test_the_cursor_falls_back_to_its_slot_when_the_download_leaves():
    widget = table("a", "b", "c")
    widget.move(1)
    widget.set_rows([row(g) for g in ("a", "c")])
    assert widget.selected_gid == "c"


def test_the_cursor_clamps_when_the_queue_shrinks_past_it():
    widget = table("a", "b", "c")
    widget.move(2)
    widget.set_rows([row("a")])
    assert widget.selected_gid == "a"
    assert widget.cursor == 0


def test_an_emptied_queue_has_no_selection():
    widget = table("a", "b")
    widget.move(1)
    widget.set_rows([])
    assert widget.selected_gid is None
    assert widget.cursor == 0


def test_the_cursor_survives_the_queue_refilling():
    widget = table("a", "b")
    widget.move(1)
    widget.set_rows([])
    widget.set_rows([row(g) for g in ("a", "b")])
    assert widget.selected_gid == "a"


def test_speed_history_still_follows_each_download_across_a_reorder():
    widget = DownloadTable(theme.THEMES["aurora"])
    first = row("a")
    first.speed = 500
    widget.set_rows([first, row("b")])
    moved = row("a")
    moved.speed = 700
    widget.set_rows([row("b"), moved])
    assert [r.history for r in widget.rows if r.gid == "a"] == [[500, 700]]


class RefusingClient(FakeClient):
    """aria2 rejecting the move, as it does when the gid has just gone."""

    def change_position(self, gid, pos, how):
        raise Aria2Error(1, f"GID {gid} is not found")


class LostClient(FakeClient):
    def change_position(self, gid, pos, how):
        raise Aria2Unreachable("connection refused")


async def press_move(cfg, client, key):
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press(key)
        await pilot.pause()
        assert app.is_running
    return app


async def test_a_refused_move_does_not_bring_the_dashboard_down(sandbox_cfg):
    """A download that finishes between the poll and the keypress used to take
    the whole dashboard with it."""
    await press_move(sandbox_cfg, RefusingClient(), "J")


async def test_a_lost_daemon_during_a_move_does_not_bring_it_down(sandbox_cfg):
    await press_move(sandbox_cfg, LostClient(), "K")


async def test_a_move_that_works_is_still_sent(sandbox_cfg):
    client = FakeClient()
    app = DlApp(sandbox_cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("J")
        await pilot.pause()
    assert client.positions == [("g1", 1, "POS_CUR")]
