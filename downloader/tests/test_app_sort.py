import pytest

from dl import history, sort
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


def status(gid, name, total, speed, done):
    return {
        "gid": gid,
        "status": "active",
        "totalLength": str(total),
        "completedLength": str(done),
        "downloadSpeed": str(speed),
        "connections": "8",
        "files": [{"path": f"/tmp/{name}", "uris": [{"uri": "https://e.com/x"}]}],
        "errorMessage": "",
    }


class MixedClient(FakeClient):
    def __init__(self):
        super().__init__()
        self.active = [
            status("g1", "charlie.iso", 100, 50, 90),
            status("g2", "alpha.iso", 300, 10, 30),
            status("g3", "bravo.iso", 200, 90, 20),
        ]


def names(app):
    return [row.name for row in app.table.rows]


async def test_the_list_starts_in_queue_order(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert names(app) == ["charlie.iso", "alpha.iso", "bravo.iso"]
        assert app.order == sort.DEFAULT


async def test_s_cycles_to_name_order(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.pause()
        assert app.order.field == "name"
        assert names(app) == ["alpha.iso", "bravo.iso", "charlie.iso"]


async def test_s_again_sorts_by_size_biggest_first(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.press("S")
        await pilot.pause()
        assert app.order == sort.Order("size", True)
        assert names(app) == ["alpha.iso", "bravo.iso", "charlie.iso"]


async def test_cycling_all_the_way_round_restores_queue_order(cfg):
    """Re-sorting what is on screen could never recover the original order."""
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        for _ in range(len(sort.FIELDS)):
            await pilot.press("S")
        await pilot.pause()
        assert app.order == sort.DEFAULT
        assert names(app) == ["charlie.iso", "alpha.iso", "bravo.iso"]


async def test_r_reverses_the_current_field(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.press("S")
        await pilot.press("R")
        await pilot.pause()
        assert app.order == sort.Order("size", False)
        assert names(app) == ["charlie.iso", "bravo.iso", "alpha.iso"]


async def test_sorting_by_speed(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        for _ in range(3):
            await pilot.press("S")
        await pilot.pause()
        assert app.order.field == "speed"
        assert names(app) == ["bravo.iso", "charlie.iso", "alpha.iso"]


async def test_sorting_by_progress(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        for _ in range(4):
            await pilot.press("S")
        await pilot.pause()
        assert app.order.field == "progress"
        assert names(app) == ["charlie.iso", "alpha.iso", "bravo.iso"]


async def test_the_order_survives_a_refresh(cfg):
    """The table is rebuilt from the daemon twice a second."""
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.pause()
        await app.refresh_data()
        await app.refresh_data()
        assert names(app) == ["alpha.iso", "bravo.iso", "charlie.iso"]


async def test_the_note_names_the_order(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.press("S")
        await pilot.pause()
        assert app.search_note.display is True
        assert "size" in app.search_note.text


async def test_the_note_is_hidden_again_in_queue_order(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.pause()
        assert app.search_note.display is True
        for _ in range(len(sort.FIELDS) - 1):
            await pilot.press("S")
        await pilot.pause()
        assert app.search_note.display is False


async def test_the_note_carries_a_filter_and_an_order_together(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "iso":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause()
        assert "iso" in app.search_note.text
        assert "name" in app.search_note.text


async def test_sorting_applies_to_the_filtered_list(cfg):
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("slash")
        await pilot.pause()
        for ch in "a":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.press("S")
        await pilot.pause()
        assert names(app) == ["alpha.iso", "bravo.iso", "charlie.iso"]


async def test_j_refuses_to_reorder_while_sorted(cfg):
    """Moving down the queue while sorted by size lands the row somewhere
    unrelated to where it was aimed."""
    client = MixedClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("S")
        await pilot.press("S")
        await pilot.press("J")
        await pilot.pause()
        assert client.positions == []


async def test_j_reorders_again_once_back_in_queue_order(cfg):
    client = MixedClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("J")
        await pilot.pause()
        assert client.positions == [("g1", 1, "POS_CUR")]


async def test_the_completed_tab_sorts_by_its_own_fields(cfg, state):
    log = state / "history.jsonl"
    for name, size in (("small.iso", 10), ("huge.iso", 900), ("mid.iso", 500)):
        history.append(
            {"name": name, "status": "ok", "bytes": size, "ts": 1, "path": ""}, log
        )
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert [r["name"] for r in app.completed.rows] == ["mid.iso", "huge.iso", "small.iso"]
        await pilot.press("S")
        await pilot.press("S")
        await pilot.pause()
        assert app.done_order.field == "size"
        assert [r["name"] for r in app.completed.rows] == ["huge.iso", "mid.iso", "small.iso"]


async def test_the_completed_tab_never_offers_speed(cfg, state):
    log = state / "history.jsonl"
    history.append({"name": "a.iso", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        for _ in range(len(sort.DONE_FIELDS) * 2):
            await pilot.press("S")
            await pilot.pause()
            assert app.done_order.field in sort.DONE_FIELDS


async def test_each_tab_keeps_its_own_order(cfg, state):
    log = state / "history.jsonl"
    history.append({"name": "a.iso", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, MixedClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        for _ in range(3):
            await pilot.press("S")
        await pilot.pause()
        assert app.order.field == "speed"
        await pilot.press("tab")
        await pilot.pause()
        assert app.done_order == sort.DONE_DEFAULT
        await pilot.press("tab")
        await pilot.pause()
        assert app.order.field == "speed"


async def test_the_completed_tab_rests_without_announcing_an_order(cfg, state):
    log = state / "history.jsonl"
    history.append({"name": "a.iso", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.search_note.display is False
