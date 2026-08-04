import pytest

from dl.tui.app import DlApp


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class FakeClient:
    def __init__(self):
        self.paused = []
        self.unpaused = []
        self.removed = []
        self.positions = []
        self.global_options = {}
        self.added = []
        self.active = [
            {
                "gid": "g1",
                "status": "active",
                "totalLength": "1000",
                "completedLength": "500",
                "downloadSpeed": "100",
                "connections": "8",
                "files": [{"path": "/tmp/a.iso", "uris": [{"uri": "https://e.com/a.iso"}]}],
            },
            {
                "gid": "g2",
                "status": "active",
                "totalLength": "2000",
                "completedLength": "100",
                "downloadSpeed": "50",
                "connections": "4",
                "files": [{"path": "/tmp/b.mkv", "uris": [{"uri": "https://e.com/b.mkv"}]}],
            },
        ]

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return []

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_global_stat(self):
        return {"downloadSpeed": "150", "numActive": "2", "numWaiting": "0", "numStopped": "5"}

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        return "g9"

    def pause(self, gid):
        self.paused.append(gid)

    def unpause(self, gid):
        self.unpaused.append(gid)

    def remove(self, gid):
        self.removed.append(gid)

    def change_position(self, gid, pos, how):
        self.positions.append((gid, pos, how))
        return 0

    def change_option(self, gid, options):
        return "OK"

    def change_global_option(self, options):
        self.global_options.update(options)
        return "OK"


async def test_app_starts_and_lists_rows(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.table.rows) == 2
        assert app.table.rows[0].name == "a.iso"


async def test_space_pauses_selected(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.paused == ["g1"]


async def test_space_on_paused_row_resumes(cfg):
    client = FakeClient()
    client.active[0]["status"] = "paused"
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.unpaused == ["g1"]


async def test_down_then_space_targets_second_row(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("space")
        assert client.paused == ["g2"]


async def test_shift_j_moves_row_down_in_queue(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("J")
        assert client.positions == [("g1", 1, "POS_CUR")]


async def test_shift_k_moves_row_up_in_queue(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("K")
        assert client.positions == [("g1", -1, "POS_CUR")]


async def test_p_pauses_all_and_u_resumes_all(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert client.paused == ["g1", "g2"]
        await pilot.press("u")
        assert client.unpaused == ["g1", "g2"]


async def test_tab_switches_to_completed_view(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.showing_completed is False
        await pilot.press("tab")
        assert app.showing_completed is True


async def test_enter_toggles_the_detail_line(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.table.expanded is False
        before = len(app.table.render_lines_count())
        await pilot.press("enter")
        assert app.table.expanded is True
        assert len(app.table.render_lines_count()) == before + 1
        await pilot.press("enter")
        assert len(app.table.render_lines_count()) == before


async def test_q_quits(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.is_running is False


async def test_lost_daemon_sets_disconnected_flag(cfg):
    from dl.rpc import Aria2Unreachable

    class DeadClient(FakeClient):
        def tell_active(self):
            raise Aria2Unreachable("gone")

        def get_global_stat(self):
            raise Aria2Unreachable("gone")

    app = DlApp(cfg, DeadClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.disconnected is True


async def test_reconnect_clears_disconnected_flag(cfg):
    from dl.rpc import Aria2Unreachable

    class FlakyClient(FakeClient):
        def __init__(self):
            super().__init__()
            self.fail = True

        def tell_active(self):
            if self.fail:
                raise Aria2Unreachable("gone")
            return self.active

    client = FlakyClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.disconnected is True
        client.fail = False
        await app.refresh_data()
        assert app.disconnected is False
