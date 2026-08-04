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
        self.removal_status = "removed"
        self.options = {}
        self.option_calls = []
        self.add_calls = []
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
        self.add_calls.append((uris, options))
        return "g9"

    def pause(self, gid):
        self.paused.append(gid)

    def unpause(self, gid):
        self.unpaused.append(gid)

    def remove(self, gid):
        self.removed.append(gid)

    def tell_status(self, gid):
        return {"gid": gid, "status": self.removal_status}

    def get_option(self, gid):
        self.option_calls.append(gid)
        return self.options.get(gid, {})

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


async def test_completed_tab_lists_history_and_is_navigable(cfg, tmp_path, monkeypatch):
    from dl import history
    from dl.tui import app as app_module

    log = tmp_path / "history.jsonl"
    for i in range(3):
        history.append(
            {"ts": 1000 + i, "name": f"f{i}.mkv", "bytes": 10, "path": "", "status": "ok"}, log
        )
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        assert [r["name"] for r in app.completed.rows] == ["f2.mkv", "f1.mkv", "f0.mkv"]
        assert app.completed.selected["name"] == "f2.mkv"
        await pilot.press("down")
        assert app.completed.selected["name"] == "f1.mkv"


async def test_open_uses_the_completed_selection_not_the_active_row(cfg, tmp_path, monkeypatch):
    from dl import history
    from dl.tui import app as app_module

    real = tmp_path / "done.mkv"
    real.write_text("data")
    log = tmp_path / "history.jsonl"
    history.append(
        {"ts": 1, "name": "done.mkv", "bytes": 4, "path": str(real), "status": "ok"}, log
    )
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    opened = []
    monkeypatch.setattr(app_module.subprocess, "run", lambda cmd, **k: opened.append(cmd))

    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("o")
        assert opened == [["open", str(real)]]
        await pilot.press("f")
        assert opened[-1] == ["open", "-R", str(real)]


async def test_delete_from_completed_list_only_keeps_the_file(cfg, tmp_path, monkeypatch):
    from dl import history
    from dl.tui import app as app_module

    real = tmp_path / "done.mkv"
    real.write_text("data")
    log = tmp_path / "history.jsonl"
    history.append(
        {"ts": 1, "name": "done.mkv", "bytes": 4, "path": str(real), "status": "ok"}, log
    )
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert app.completed.rows == []
        assert real.exists()


async def test_delete_from_completed_with_disk_removes_file_and_sidecar(cfg, tmp_path, monkeypatch):
    from dl import history
    from dl.tui import app as app_module

    real = tmp_path / "done.mkv"
    real.write_text("data")
    sidecar = tmp_path / "done.mkv.aria2"
    sidecar.write_text("ctl")
    log = tmp_path / "history.jsonl"
    history.append(
        {"ts": 1, "name": "done.mkv", "bytes": 4, "path": str(real), "status": "ok"}, log
    )
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert app.completed.rows == []
        assert not real.exists()
        assert not sidecar.exists()


async def test_delete_modal_escape_changes_nothing(cfg, tmp_path, monkeypatch):
    from dl import history
    from dl.tui import app as app_module

    real = tmp_path / "done.mkv"
    real.write_text("data")
    log = tmp_path / "history.jsonl"
    history.append(
        {"ts": 1, "name": "done.mkv", "bytes": 4, "path": str(real), "status": "ok"}, log
    )
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.completed.rows) == 1
        assert real.exists()


async def test_delete_active_with_disk_removes_partial_file(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    partial = tmp_path / "a.iso"
    partial.write_text("half")
    (tmp_path / "a.iso.aria2").write_text("ctl")

    client = FakeClient()
    client.active[0]["files"][0]["path"] = str(partial)
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert client.removed == ["g1"]
        assert not partial.exists()
        assert not (tmp_path / "a.iso.aria2").exists()


def type_url(pilot, app, url):
    """Fill the add modal and press its Queue button."""
    from textual.widgets import Button, TextArea

    app.screen.query_one("#urls", TextArea).text = url
    app.screen.query_one("#ok", Button).press()


async def test_adding_a_fresh_url_never_prompts(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        type_url(pilot, app, "https://e.com/brand-new.mkv")
        await pilot.pause()
        assert client.added == ["https://e.com/brand-new.mkv"]


async def test_adding_a_url_whose_file_exists_prompts(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module
    from dl.tui.modals import DuplicateModal

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    target = cfg.categories["video"].dir / "dup.mkv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already here")

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        type_url(pilot, app, "https://e.com/dup.mkv")
        await pilot.pause()
        assert isinstance(app.screen, DuplicateModal)
        assert client.added == [], "queued before the question was answered"


async def test_skipping_from_the_dashboard_queues_nothing(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    target = cfg.categories["video"].dir / "dup.mkv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("already here")

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        type_url(pilot, app, "https://e.com/dup.mkv")
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert client.added == []
    assert target.read_text() == "already here"


async def test_renaming_from_the_dashboard_sends_the_rename_options(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    target = cfg.categories["video"].dir / "dup.mkv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("keep me")

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        type_url(pilot, app, "https://e.com/dup.mkv")
        await pilot.pause()
        await pilot.press("r")
        await pilot.pause()
        assert len(client.add_calls) == 1
        options = client.add_calls[0][1]
        assert options["auto-file-renaming"] == "true"
        assert options["continue"] == "false"
    assert target.read_text() == "keep me"


async def test_overwriting_from_the_dashboard_clears_the_old_file(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    target = cfg.categories["video"].dir / "dup.mkv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("old")
    target.with_name("dup.mkv.aria2").write_text("ctl")

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        type_url(pilot, app, "https://e.com/dup.mkv")
        await pilot.pause()
        await pilot.press("o")
        await pilot.pause()
        for _ in range(40):
            await pilot.pause()
            if not target.exists():
                break
        assert not target.exists()
        assert not target.with_name("dup.mkv.aria2").exists()
        assert client.add_calls[0][1]["allow-overwrite"] == "true"


async def test_a_proxied_download_is_badged_in_the_table(cfg):
    client = FakeClient()
    client.options = {"g1": {"all-proxy": "http://127.0.0.1:2080"}}

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        by_gid = {row.gid: row for row in app.table.rows}
        assert by_gid["g1"].proxied is True
        assert by_gid["g2"].proxied is False


async def test_an_http_proxy_option_also_counts_as_proxied(cfg):
    """An older daemon that inherited http_proxy really is proxying."""
    client = FakeClient()
    client.options = {"g1": {"http-proxy": "http://127.0.0.1:2080"}}

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert {r.gid: r.proxied for r in app.table.rows}["g1"] is True


async def test_an_empty_proxy_option_is_not_proxied(cfg):
    client = FakeClient()
    client.options = {"g1": {"all-proxy": ""}}

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert {r.gid: r.proxied for r in app.table.rows}["g1"] is False


async def test_options_are_fetched_once_per_download(cfg):
    """Options cannot change under us, and the table refreshes twice a second."""
    client = FakeClient()

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(4):
            await app.refresh_data()
        assert sorted(set(client.option_calls)) == ["g1", "g2"]
        assert len(client.option_calls) == 2


async def test_a_failed_option_lookup_leaves_the_row_unbadged(cfg):
    from dl.rpc import Aria2Error

    class Grumpy(FakeClient):
        def get_option(self, gid):
            raise Aria2Error(1, "nope")

    app = DlApp(cfg, Grumpy())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert all(row.proxied is False for row in app.table.rows)


async def test_the_proxy_cache_forgets_downloads_that_are_gone(cfg):
    client = FakeClient()

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert set(app.proxied) == {"g1", "g2"}
        client.active = [client.active[0]]
        await app.refresh_data()
        assert set(app.proxied) == {"g1"}


async def test_delete_active_waits_for_the_removal_to_settle(cfg, tmp_path, monkeypatch):
    """aria2 rewrites the control file as it winds a download down, so unlinking
    before the removal settles leaves the .aria2 sidecar behind."""
    from dl.tui import app as app_module

    partial = tmp_path / "a.iso"
    partial.write_text("half")
    sidecar = tmp_path / "a.iso.aria2"
    sidecar.write_text("ctl")

    client = FakeClient()
    client.removal_status = "active"
    client.active[0]["files"][0]["path"] = str(partial)
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        assert client.removed == ["g1"]
        assert partial.exists(), "unlinked while aria2 was still winding down"

        client.removal_status = "removed"
        for _ in range(40):
            await pilot.pause()
            if not partial.exists():
                break
        assert not partial.exists()
        assert not sidecar.exists()


async def test_delete_active_gives_up_waiting_when_the_daemon_goes_away(
    cfg, tmp_path, monkeypatch
):
    from dl.rpc import Aria2Unreachable
    from dl.tui import app as app_module

    partial = tmp_path / "a.iso"
    partial.write_text("half")

    class GoneClient(FakeClient):
        def tell_status(self, gid):
            raise Aria2Unreachable("gone")

    client = GoneClient()
    client.active[0]["files"][0]["path"] = str(partial)
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("d")
        for _ in range(40):
            await pilot.pause()
            if not partial.exists():
                break
        assert not partial.exists()


async def test_delete_active_list_only_keeps_partial_file(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    partial = tmp_path / "a.iso"
    partial.write_text("half")

    client = FakeClient()
    client.active[0]["files"][0]["path"] = str(partial)
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert client.removed == ["g1"]
        assert partial.exists()


async def test_hint_line_changes_with_the_tab(cfg, tmp_path, monkeypatch):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        assert "tab active" in app.hint_text
        await pilot.press("tab")
        assert "pause/resume" in app.hint_text


async def test_filter_items_is_identity_by_default(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        items = [{"gid": "x"}, {"gid": "y"}]
        assert app._filter_items(items) == items


async def test_base_app_shows_splash_when_empty(cfg):
    client = FakeClient()
    client.active = []
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.splash_when_empty is True
        assert app.table.rows == []


async def test_after_refresh_runs_on_a_successful_poll(cfg):
    seen = []

    class Probe(DlApp):
        def _after_refresh(self, items):
            seen.append(len(items))

    app = Probe(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert seen and seen[-1] == 2


async def test_after_refresh_is_skipped_when_the_daemon_is_unreachable(cfg):
    from dl.rpc import Aria2Unreachable

    seen = []

    class Dead(DlApp):
        def _after_refresh(self, items):
            seen.append(items)

    class DeadClient(FakeClient):
        def tell_active(self):
            raise Aria2Unreachable("gone")

    app = Dead(cfg, DeadClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        seen.clear()
        await app.refresh_data()
        assert seen == []
        assert app.disconnected is True


async def test_filter_items_narrows_what_reaches_the_table(cfg):
    class OnlyG2(DlApp):
        def _filter_items(self, items):
            return [i for i in items if i["gid"] == "g2"]

    app = OnlyG2(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert [r.gid for r in app.table.rows] == ["g2"]


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


async def test_l_applies_the_limit_to_the_selected_download_only(cfg, monkeypatch, tmp_path):
    from dl.tui import app as app_module
    from textual.widgets import Input

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    client = FakeClient()
    applied = []
    client.change_option = lambda gid, opts: applied.append((gid, opts))

    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        app.screen.query_one("#rate", Input).value = "500K"
        await pilot.press("enter")
        await pilot.pause()

    assert applied == [("g1", {"max-download-limit": "500K"})]
    assert client.global_options == {}


async def test_there_is_no_global_limit_key(cfg, monkeypatch, tmp_path):
    """`L` used to cap every download at once; that option is gone."""
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("L")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert client.global_options == {}


async def test_limit_on_an_empty_queue_does_nothing(cfg, monkeypatch, tmp_path):
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    client = FakeClient()
    client.active = []
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("l")
        await pilot.pause()
        assert len(app.screen_stack) == 1
