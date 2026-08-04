import pytest

from dl.rpc import Aria2Unreachable
from dl.tui.preview import PreviewApp, Request, summarise


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def status(gid, state="active", **over):
    base = {
        "gid": gid,
        "status": state,
        "totalLength": "1000",
        "completedLength": "500",
        "downloadSpeed": "100",
        "connections": "4",
        "files": [{"path": f"/tmp/{gid}.iso", "uris": [{"uri": f"https://e.com/{gid}.iso"}]}],
        "errorMessage": "",
    }
    base.update(over)
    return base


class PreviewClient:
    def __init__(self, active=("g1", "g2"), waiting=()):
        self.active = [status(g) for g in active]
        self.waiting = [status(g, "waiting") for g in waiting]
        self.paused = []
        self.final = {}
        self.fail = False

    def tell_active(self):
        if self.fail:
            raise Aria2Unreachable("gone")
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        if self.fail:
            raise Aria2Unreachable("gone")
        return self.waiting

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_global_stat(self):
        if self.fail:
            raise Aria2Unreachable("gone")
        return {"downloadSpeed": "100", "numActive": "2", "numWaiting": "0", "numStopped": "0"}

    def tell_status(self, gid):
        return self.final.get(gid, status(gid, "complete", completedLength="1000"))

    def pause(self, gid):
        self.paused.append(gid)

    def unpause(self, gid):
        pass


def result(**over):
    base = {"name": "a.iso", "status": "complete", "bytes": 6127219712, "seconds": 683, "error": ""}
    base.update(over)
    return base


def test_summarise_empty_is_empty():
    assert summarise([]) == []


def test_summarise_success_shows_size_duration_and_average():
    line = summarise([result()])[0]
    assert "✅" in line
    assert "a.iso" in line
    assert "5.7 GB" in line
    assert "11m 23s" in line
    assert "8.6 MB/s" in line


def test_summarise_success_without_duration_omits_the_average():
    line = summarise([result(seconds=0)])[0]
    assert "5.7 GB" in line
    assert "/s" not in line


def test_summarise_error_shows_the_message():
    line = summarise([result(status="error", error="HTTP 403")])[0]
    assert "❌" in line
    assert "a.iso" in line
    assert "HTTP 403" in line


def test_summarise_error_without_message_says_failed():
    assert "failed" in summarise([result(status="error", error="")])[0]


def test_summarise_removed_is_reported():
    line = summarise([result(status="removed")])[0]
    assert "a.iso" in line
    assert "removed" in line


def test_summarise_running_collapses_into_one_trailing_line():
    lines = summarise([result(status="active"), result(name="b.mkv", status="waiting")])
    assert len(lines) == 1
    assert "2 still downloading" in lines[0]
    assert "dl ls" in lines[0]


def test_summarise_singular_wording_for_one_running():
    assert "1 still downloading" in summarise([result(status="active")])[0]


def test_summarise_mixed_lists_finished_then_running():
    lines = summarise(
        [
            result(),
            result(name="b.mkv", status="error", error="boom"),
            result(name="c.zip", status="active"),
        ]
    )
    assert len(lines) == 3
    assert "a.iso" in lines[0]
    assert "b.mkv" in lines[1]
    assert "1 still downloading" in lines[2]


def test_summarise_ascii_mode_uses_no_emoji():
    lines = summarise(
        [
            result(),
            result(name="b.mkv", status="error", error="boom"),
            result(name="c.zip", status="active"),
        ],
        icons=False,
    )
    joined = " ".join(lines)
    assert "✅" not in joined and "❌" not in joined and "⏳" not in joined
    assert "[ok]" in joined
    assert "[fail]" in joined
    assert "[...]" in joined


def test_summarise_never_emits_markup_that_would_break_a_terminal():
    for line in summarise([result(), result(status="active")]):
        assert "\x1b[" not in line


def test_summarise_unnamed_result_has_a_placeholder():
    assert "(unnamed)" in summarise([result(name="")])[0]


async def test_preview_shows_only_the_watched_gids(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g1", "g3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert sorted(r.gid for r in app.table.rows) == ["g1", "g3"]


async def test_preview_pauses_only_the_selected_watched_gid(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g2"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.paused == ["g2"]


async def test_preview_pause_all_covers_only_the_watch_set(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g1", "g3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert sorted(client.paused) == ["g1", "g3"]


async def test_preview_exits_once_every_watched_gid_settles(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
    assert app.is_running is False
    assert [r["status"] for r in app.results] == ["complete"]


async def test_preview_stays_while_one_gid_is_still_waiting(cfg):
    client = PreviewClient(active=("g1",), waiting=("g2",))
    app = PreviewApp(cfg, client, ["g1", "g2"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True


async def test_preview_does_not_exit_when_the_daemon_is_unreachable(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.fail = True
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True
        assert app.disconnected is True
        assert app.results == []


async def test_preview_never_renders_the_splash(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g9"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.splash_when_empty is False
        assert "d o w n l o a d e r" not in app.table.text


async def test_preview_collects_error_results(cfg):
    client = PreviewClient(active=("g1",))
    client.final["g1"] = status("g1", "error", errorMessage="HTTP 403")
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
    assert app.results[0]["status"] == "error"
    assert app.results[0]["error"] == "HTTP 403"


async def test_preview_hint_replaces_the_dashboard_hint(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "detach" in app.hint_text
        assert "add" not in app.hint_text


async def test_preview_ignores_the_add_key(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_preview_ignores_the_completed_tab_key(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.showing_completed is False


async def test_preview_ignores_the_reorder_keys(cfg):
    class Reorderable(PreviewClient):
        def __init__(self):
            super().__init__(active=("g1",))
            self.positions = []

        def change_position(self, gid, pos, how):
            self.positions.append(gid)
            return 0

    client = Reorderable()
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("J")
        await pilot.press("K")
        assert client.positions == []


def request(tmp_path, cfg, name="movie.mkv"):
    return Request(
        url=f"https://e.com/{name}",
        filename=name,
        default_dir=tmp_path / "default",
        category=cfg.categories["video"],
    )


async def test_picking_shows_one_screen_per_pending_file(cfg, tmp_path):
    client = PreviewClient(active=())
    seen = []

    def queue(chosen):
        seen.append(list(chosen))
        return []

    app = PreviewApp(
        cfg,
        client,
        pending=[request(tmp_path, cfg, "a.mkv"), request(tmp_path, cfg, "b.mkv")],
        queue=queue,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.picking is True
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert seen == [[tmp_path / "default", tmp_path / "default"]]


async def test_picking_does_not_exit_the_app_before_queuing(cfg, tmp_path):
    client = PreviewClient(active=())
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: [])
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True
        assert app.picking is True


async def test_escape_records_none_and_moves_on(cfg, tmp_path):
    client = PreviewClient(active=())
    seen = []
    app = PreviewApp(
        cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: seen.append(list(c)) or []
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert seen == [[None]]


async def test_queue_result_becomes_the_watch_set(cfg, tmp_path):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.picking is False
        assert app.watch == {"g1"}
        assert app.is_running is True


async def test_app_exits_when_queuing_produced_nothing(cfg, tmp_path):
    client = PreviewClient(active=())
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: [])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.is_running is False


async def test_ctrl_c_in_the_picker_queues_nothing_and_exits(cfg, tmp_path):
    client = PreviewClient(active=())
    seen = []
    app = PreviewApp(
        cfg,
        client,
        pending=[request(tmp_path, cfg, "a.mkv"), request(tmp_path, cfg, "b.mkv")],
        queue=lambda c: seen.append(list(c)) or ["g1"],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert seen == []
    assert app.cancelled is True
    assert app.watch == set()
    assert app.is_running is False


async def test_tab_completes_inside_the_picker(cfg, tmp_path):
    """Priority bindings resolve app-first, so the dashboard's tab binding would
    otherwise swallow the key before the picker ever sees it."""
    client = PreviewClient(active=())
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: [])
    async with app.run_test() as pilot:
        await pilot.pause()
        picker = app.screen
        await pilot.press("tab")
        await pilot.pause()
        assert str(tmp_path / "default") in picker.input_value


async def test_cancelling_stops_the_remaining_pickers(cfg, tmp_path):
    """The second file must not be asked about after the batch is abandoned."""
    client = PreviewClient(active=())
    app = PreviewApp(
        cfg,
        client,
        pending=[request(tmp_path, cfg, "a.mkv"), request(tmp_path, cfg, "b.mkv")],
        queue=lambda c: [],
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert app.chosen == []


def stub_app(monkeypatch, *, cancelled, results):
    from dl.tui import preview as preview_module

    class Stub:
        def __init__(self, *a, **k):
            self.cancelled = cancelled
            self.results = results

        def run(self):
            return None

    monkeypatch.setattr(preview_module, "PreviewApp", Stub)
    return preview_module


async def test_run_preview_reports_cancellation(cfg, monkeypatch):
    module = stub_app(monkeypatch, cancelled=True, results=[])
    lines, cancelled = module.run_preview(cfg, PreviewClient(), pending=[1], queue=None)
    assert cancelled is True
    assert any("cancelled" in line for line in lines)


async def test_run_preview_reports_completion(cfg, monkeypatch):
    done = [{"name": "a.mkv", "status": "complete", "bytes": 1024, "seconds": 0}]
    module = stub_app(monkeypatch, cancelled=False, results=done)
    lines, cancelled = module.run_preview(cfg, PreviewClient(), gids=["g1"])
    assert cancelled is False
    assert any("a.mkv" in line for line in lines)


async def test_gids_only_construction_still_works(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.picking is False
        assert app.watch == {"g1"}
