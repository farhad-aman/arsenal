"""Telling torrents apart from everything else, and from their own scaffolding."""

import base64

import pytest

from dl import torrent

MAGNET = "magnet:?xt=urn:btih:481b6e3617be4c88f96cb25e47c9d8272130071e"


def status(**over) -> dict:
    base = {
        "gid": "g1",
        "status": "active",
        "files": [{"path": "/tmp/debian.iso", "uris": []}],
        "bittorrent": {"mode": "single", "info": {"name": "debian.iso"}},
    }
    base.update(over)
    return base


def test_a_magnet_is_recognised():
    assert torrent.is_magnet(MAGNET)
    assert torrent.is_magnet("MAGNET:?xt=urn:btih:abc")
    assert torrent.is_magnet("  " + MAGNET)


def test_a_plain_url_is_not_a_magnet():
    assert not torrent.is_magnet("https://e.com/a.iso")


def test_a_torrent_on_disk_is_recognised(tmp_path):
    path = tmp_path / "x.torrent"
    path.write_bytes(b"d4:infod4:name1:aee")
    assert torrent.is_torrent_file(str(path))


def test_a_torrent_url_is_not_a_local_file():
    """aria2 fetches a remote .torrent and follows it without help."""
    assert not torrent.is_torrent_file("https://e.com/thing.torrent")


def test_a_torrent_path_that_does_not_exist_is_not_a_file(tmp_path):
    assert not torrent.is_torrent_file(str(tmp_path / "missing.torrent"))


def test_is_torrent_covers_both_forms(tmp_path):
    path = tmp_path / "x.torrent"
    path.write_bytes(b"d4:infoe")
    assert torrent.is_torrent(MAGNET)
    assert torrent.is_torrent(str(path))
    assert not torrent.is_torrent("https://e.com/a.iso")


def test_a_torrent_is_encoded_for_the_daemon(tmp_path):
    path = tmp_path / "x.torrent"
    path.write_bytes(b"d4:infod4:name1:aee")
    assert base64.b64decode(torrent.encoded(path)) == b"d4:infod4:name1:aee"


def test_the_torrent_names_itself():
    assert torrent.name_of(status()) == "debian.iso"


def test_a_download_with_no_torrent_has_no_torrent_name():
    assert torrent.name_of({"files": [{"path": "/tmp/a.iso"}]}) == ""


def test_a_metadata_placeholder_is_spotted_by_its_name():
    meta = status(
        files=[{"path": "/tmp/[METADATA]481b6e", "uris": []}],
        bittorrent={"info": {"name": "[METADATA]481b6e"}},
    )
    assert torrent.is_metadata(meta)


def test_anything_that_handed_off_is_scaffolding():
    """followedBy means aria2 replaced this download with the real one."""
    assert torrent.is_metadata(status(status="complete", followedBy=["g2"]))


def test_a_real_torrent_is_not_metadata():
    assert not torrent.is_metadata(status())


def test_a_plain_download_is_not_metadata():
    assert not torrent.is_metadata({"files": [{"path": "/tmp/a.iso"}]})


def test_a_torrent_status_is_recognised():
    assert torrent.is_torrent_status(status())
    assert not torrent.is_torrent_status({"files": []})


def test_a_single_file_torrent_lands_as_that_file(tmp_path):
    assert torrent.target(status(), tmp_path) == tmp_path.__class__("/tmp/debian.iso")


def test_a_multi_file_torrent_lands_as_a_folder(tmp_path):
    many = status(
        files=[{"path": f"/tmp/Album/track{i}.flac"} for i in range(3)],
        bittorrent={"mode": "multi", "info": {"name": "Album"}},
    )
    assert torrent.target(many, tmp_path) == tmp_path / "Album"


def test_a_multi_file_torrent_with_no_name_falls_back_to_the_folder(tmp_path):
    many = status(files=[{"path": "/tmp/a"}], bittorrent={"mode": "multi", "info": {}})
    assert torrent.target(many, tmp_path) == tmp_path


def test_a_single_file_torrent_with_no_path_yet_uses_its_name(tmp_path):
    early = status(files=[])
    assert torrent.target(early, tmp_path) == tmp_path / "debian.iso"


@pytest.mark.parametrize("value", ["", "   ", "not a url"])
def test_rubbish_is_not_a_torrent(value):
    assert not torrent.is_torrent(value)


def test_the_daemon_is_told_to_stop_when_the_download_is_done(tmp_path):
    """Seeding was a choice, and the choice was not to."""
    from dl import config, daemon

    args = daemon.aria2_args(config.defaults(), tmp_path, 6810, "secret")
    assert "--seed-time=0" in args


def test_the_daemon_joins_the_dht(tmp_path):
    """Most magnets carry no trackers and never resolve without it."""
    from dl import config, daemon

    args = daemon.aria2_args(config.defaults(), tmp_path, 6810, "secret")
    assert "--enable-dht=true" in args
    assert any(a.startswith("--dht-listen-port=") for a in args)
    assert any(a.startswith("--listen-port=") for a in args)


def test_the_dht_table_is_kept_with_the_rest_of_the_state(tmp_path):
    from dl import config, daemon

    args = daemon.aria2_args(config.defaults(), tmp_path, 6810, "secret")
    assert f"--dht-file-path={tmp_path / 'dht.dat'}" in args


def test_the_daemon_follows_a_torrent_into_its_transfer(tmp_path):
    from dl import config, daemon

    args = daemon.aria2_args(config.defaults(), tmp_path, 6810, "secret")
    assert "--follow-torrent=true" in args


def test_add_torrent_sends_the_file_and_records_the_time(tmp_path, monkeypatch):
    from dl import started
    from dl.rpc import Aria2

    blob = tmp_path / "x.torrent"
    blob.write_bytes(b"d4:infod4:name1:aee")
    sent = {}

    def fake_call(self, method, *params):
        sent["method"] = method
        sent["params"] = params
        return "gidT"

    monkeypatch.setattr(Aria2, "_call", fake_call)
    client = Aria2("127.0.0.1", 1, "secret", state=tmp_path)
    assert client.add_torrent(blob, {"dir": "/tmp"}) == "gidT"
    assert sent["method"] == "aria2.addTorrent"
    assert base64.b64decode(sent["params"][0]) == b"d4:infod4:name1:aee"
    assert sent["params"][1] == []
    assert sent["params"][2] == {"dir": "/tmp"}
    assert started.when(tmp_path, "gidT") > 0


def test_the_command_line_hands_a_local_torrent_to_add_torrent(tmp_path, sandbox_cfg):
    """addUri takes somewhere to fetch from; this is already here."""
    from dl import cli

    blob = tmp_path / "x.torrent"
    blob.write_bytes(b"d4:infod4:name1:aee")
    seen = {}

    class Client:
        def add_torrent(self, path, options):
            seen["torrent"] = path
            return "gidT"

        def add_uri(self, uris, options):
            seen["uri"] = uris
            return "gidU"

    rc, gids = cli.cmd_add([str(blob)], sandbox_cfg, Client(), None)
    assert rc == 0 and gids == ["gidT"]
    assert seen["torrent"] == blob
    assert "uri" not in seen


def test_a_magnet_still_goes_through_add_uri(sandbox_cfg):
    from dl import cli

    seen = {}

    class Client:
        def add_uri(self, uris, options):
            seen["uri"] = uris
            return "gidU"

    rc, gids = cli.cmd_add([MAGNET], sandbox_cfg, Client(), None)
    assert rc == 0 and gids == ["gidU"]
    assert seen["uri"] == [MAGNET]


def test_a_remote_torrent_url_still_goes_through_add_uri(sandbox_cfg):
    """aria2 fetches it and follows it into the transfer by itself."""
    from dl import cli

    seen = {}

    class Client:
        def add_uri(self, uris, options):
            seen["uri"] = uris
            return "gidU"

    rc, gids = cli.cmd_add(["https://e.com/thing.torrent"], sandbox_cfg, Client(), None)
    assert rc == 0 and seen["uri"] == ["https://e.com/thing.torrent"]


def _complete(tmp_path, name="debian.iso", where=None):
    landed = (where or tmp_path) / name
    landed.parent.mkdir(parents=True, exist_ok=True)
    landed.write_bytes(b"x" * 10)
    return landed


def test_a_plain_download_still_uses_the_url_to_decide_it_was_not_pinned(tmp_path, sandbox_cfg):
    """The existing rule, unchanged: an http download that is not where its own
    URL would have put it was pinned."""
    from dl import hook

    pinned = tmp_path / "pinned"
    landed = _complete(tmp_path, where=pinned)
    assert hook.relocate(landed, sandbox_cfg, "https://e.com/debian.iso") == landed


def test_the_seeding_stop_travels_with_the_download(sandbox_cfg, tmp_path):
    """A daemon started before torrents existed never saw --seed-time=0, and
    dl adopts a running daemon rather than restarting it."""
    from dl import cli
    from dl.routing import OTHER

    options = cli.add_options(sandbox_cfg, cli.Resolution(tmp_path, OTHER))
    assert options["seed-time"] == "0"


def test_the_arguments_a_daemon_started_with_are_remembered(tmp_path):
    from dl import config, daemon

    args = daemon.aria2_args(config.defaults(), tmp_path, 6810, "secret")
    assert daemon.args_signature(args) == daemon.args_signature(list(args))
    assert daemon.args_signature(args) != daemon.args_signature(args + ["--enable-dht=true"])


def test_a_daemon_with_the_arguments_it_wanted_is_left_alone(tmp_path, monkeypatch):
    """Restarting on every command would be worse than the problem."""
    from dl import config, daemon

    cfg = config.defaults()
    daemon.write_signature(tmp_path, daemon.args_signature(daemon.aria2_args(cfg, tmp_path, daemon.PORT, "s")))
    restarted = []
    monkeypatch.setattr(daemon, "_spawn", lambda *a: restarted.append(1))

    class Idle:
        def tell_active(self):
            return []

        def tell_waiting(self):
            return []

    assert daemon.stop_if_stale(cfg, tmp_path, "s", Idle()) is False
    assert restarted == []


def test_a_daemon_started_before_a_setting_existed_is_stopped(tmp_path, monkeypatch):
    """The DHT cannot be turned on over RPC, so a daemon that predates it never
    gets it however long dl runs. Stopping it is all this does — starting the
    replacement belongs to the path that knows how to fail out loud."""
    from dl import config, daemon

    daemon.write_signature(tmp_path, "from-an-older-dl")
    daemon.write_pid(tmp_path, 4242)
    stopped = []
    monkeypatch.setattr(daemon, "_terminate", lambda pid, wait=5.0: stopped.append(pid))

    class Idle:
        def tell_active(self):
            return []

        def tell_waiting(self):
            return []

    assert daemon.stop_if_stale(config.defaults(), tmp_path, "s", Idle()) is True
    assert stopped == [4242]
    assert daemon.read_pid(tmp_path) == 0


def test_a_busy_daemon_is_never_stopped_under_a_download(tmp_path, monkeypatch):
    from dl import config, daemon

    daemon.write_signature(tmp_path, "from-an-older-dl")
    restarted = []
    monkeypatch.setattr(daemon, "_spawn", lambda *a: restarted.append(1))

    class Busy:
        def tell_active(self):
            return [{"gid": "g1"}]

        def tell_waiting(self):
            return []

    assert daemon.stop_if_stale(config.defaults(), tmp_path, "s", Busy()) is False
    assert restarted == []


def test_an_unreachable_daemon_is_not_stopped_from_here(tmp_path, monkeypatch):
    from dl import config, daemon
    from dl.rpc import Aria2Unreachable

    daemon.write_signature(tmp_path, "older")
    restarted = []
    monkeypatch.setattr(daemon, "_spawn", lambda *a: restarted.append(1))

    class Gone:
        def tell_active(self):
            raise Aria2Unreachable("refused")

        def tell_waiting(self):
            return []

    assert daemon.stop_if_stale(config.defaults(), tmp_path, "s", Gone()) is False
    assert restarted == []


def test_spawning_records_the_signature(tmp_path, monkeypatch):
    from dl import config, daemon

    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 7})())
    cfg = config.defaults()
    daemon._spawn(cfg, tmp_path, daemon.PORT, "s")
    wanted = daemon.args_signature(daemon.aria2_args(cfg, tmp_path, daemon.PORT, "s"))
    assert daemon.read_signature(tmp_path) == wanted


def test_a_restart_that_cannot_bind_is_an_error_not_a_dead_client(tmp_path, monkeypatch):
    """It killed the daemon, could not bind the port back, and handed out a
    client pointing at nothing. Everything downstream then raised."""
    from dl import config, daemon

    daemon.write_signature(tmp_path, "from-an-older-dl")
    daemon.write_pid(tmp_path, 4242)
    monkeypatch.setattr(daemon.shutil, "which", lambda _n: "/usr/bin/aria2c")
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "ours")
    monkeypatch.setattr(daemon, "_terminate", lambda pid, wait=5.0: None)
    monkeypatch.setattr(daemon, "_wait_bindable", lambda port, timeout: False)
    monkeypatch.setattr(daemon, "_retire_wanderer", lambda state, secret: None)
    monkeypatch.setattr(daemon, "alive", lambda pid: False)

    class Idle:
        def tell_active(self):
            return []

        def tell_waiting(self):
            return []

    monkeypatch.setattr(daemon, "Aria2", lambda *a, **k: Idle())
    with pytest.raises(daemon.DaemonStartFailed):
        daemon.ensure_running(config.defaults(), tmp_path)


def test_a_port_just_released_is_still_bindable(tmp_path):
    """A daemon that has only just stopped leaves its socket in TIME_WAIT, and
    a probe without SO_REUSEADDR reads that as somebody else holding the port."""
    import socket

    from dl import daemon

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    holder.listen(1)
    holder.close()
    assert daemon._bindable(port) is True


async def test_the_preview_survives_a_daemon_that_went_away(sandbox_cfg, tmp_path, monkeypatch):
    """Queuing runs inside the preview's own event loop, so an error there is a
    traceback across the terminal rather than a message."""
    from dl.rpc import Aria2Unreachable
    from dl.tui import preview as preview_module
    from dl.tui.preview import PreviewApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(preview_module, "STATE_DIR", tmp_path)

    def boom(chosen, decisions=None):
        raise Aria2Unreachable("connection refused")

    app = PreviewApp(sandbox_cfg, FakeClient(), queue=boom, pick_paths=False)
    async with app.run_test() as pilot:
        await pilot.pause()
        app._start_queue()
        await pilot.pause()
    assert app.failed


async def test_the_preview_follows_a_torrent_into_its_transfer(sandbox_cfg, tmp_path, monkeypatch):
    """A .torrent or a magnet completes in seconds and aria2 starts a different
    gid for the actual download. The preview closed on the first one, so it
    shut just as the transfer began and you had to run dl again."""
    from dl.tui import preview as preview_module
    from dl.tui.preview import PreviewApp

    monkeypatch.setattr(preview_module, "STATE_DIR", tmp_path)

    class Handing:
        """The .torrent is gone from the queue; the transfer it started is on it."""

        def __init__(self):
            self.handed = False

        def tell_active(self):
            if not self.handed:
                return []
            return [{"gid": "child", "status": "active", "following": "parent",
                     "totalLength": "100", "completedLength": "10", "downloadSpeed": "5",
                     "connections": "8", "files": [{"path": "/tmp/x.iso", "uris": []}]}]

        def tell_waiting(self):
            return []

        def get_global_stat(self):
            return {"downloadSpeed": "0", "numActive": "0", "numWaiting": "0", "numStopped": "0"}

        def get_option(self, gid):
            return {}

        def tell_status(self, gid):
            if gid == "parent":
                self.handed = True
                return {"gid": "parent", "status": "complete", "followedBy": ["child"],
                        "files": [{"path": "/tmp/x.torrent", "uris": []}]}
            return {"gid": "child", "status": "active", "following": "parent",
                    "files": [{"path": "/tmp/x.iso", "uris": []}]}

    app = PreviewApp(sandbox_cfg, Handing(), gids=["parent"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.pause()
        assert "child" in app.watch, "the transfer it handed off to was not picked up"
        assert "parent" not in app.watch, "the .torrent itself is not the download"
        assert app.is_running, "the preview closed as the download started"


def test_the_torrent_file_goes_when_the_download_finishes(tmp_path):
    """Fetching a .torrent over http leaves it in the destination beside the
    thing it described, which nobody asked to download."""
    from dl import hook

    blob = tmp_path / "debian.torrent"
    blob.write_bytes(b"d4:infoe")

    class Client:
        def tell_status(self, gid):
            return {"gid": "parent", "files": [{"path": str(blob), "uris": []}]}

    assert hook.drop_source_torrent(Client(), {"following": "parent"}) is True
    assert not blob.exists()


def test_a_magnets_placeholder_is_not_mistaken_for_a_torrent_file(tmp_path):
    from dl import hook

    class Client:
        def tell_status(self, gid):
            return {"gid": "p", "files": [{"path": str(tmp_path / "[METADATA]481b"), "uris": []}]}

    assert hook.drop_source_torrent(Client(), {"following": "p"}) is False


def test_a_download_that_followed_nothing_has_no_torrent_to_drop():
    from dl import hook

    class Client:
        def tell_status(self, gid):
            raise AssertionError("should not be asked")

    assert hook.drop_source_torrent(Client(), {}) is False


def test_a_vanished_parent_is_quiet(tmp_path):
    from dl import hook
    from dl.rpc import Aria2Error

    class Client:
        def tell_status(self, gid):
            raise Aria2Error(1, "not found")

    assert hook.drop_source_torrent(Client(), {"following": "gone"}) is False


def test_deleting_a_torrent_takes_the_torrent_file_with_it(tmp_path):
    """d then 'from disk' left the .torrent aria2 fetched to start it."""
    from dl.tui import queueing

    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"x" * 10)
    blob = tmp_path / "debian.iso.torrent"
    blob.write_bytes(b"d4:infoe")

    class Client:
        def tell_status(self, gid):
            if gid == "child":
                return {"gid": "child", "following": "parent"}
            return {"gid": "parent", "files": [{"path": str(blob), "uris": []}]}

    queueing.drop_source_torrent(Client(), "child")
    assert not blob.exists()


def test_deleting_a_multi_file_torrent_removes_the_whole_folder(tmp_path):
    """A multi-file torrent is a folder, and unlink cannot remove one — the
    call raised, was swallowed, and every file stayed."""
    from dl.tui import queueing

    folder = tmp_path / "Some Album"
    folder.mkdir()
    (folder / "a.flac").write_bytes(b"x")
    (folder / "b.flac").write_bytes(b"y")

    queueing.unlink_download(folder)
    assert not folder.exists()


def test_deleting_a_single_file_is_unchanged(tmp_path):
    from dl.tui import queueing

    landed = tmp_path / "a.iso"
    landed.write_bytes(b"x")
    control = tmp_path / "a.iso.aria2"
    control.write_bytes(b"c")
    queueing.unlink_download(landed)
    assert not landed.exists() and not control.exists()


def test_dropping_a_source_torrent_for_a_plain_download_does_nothing(tmp_path):
    from dl.tui import queueing

    class Client:
        def tell_status(self, gid):
            return {"gid": gid}

    assert queueing.drop_source_torrent(Client(), "g1") is False


async def test_delete_from_disk_clears_the_torrent_too(sandbox_cfg, tmp_path, monkeypatch):
    """End to end through the key: d, then 'from disk'."""
    from textual.widgets import Button

    from dl.tui import app as app_module
    from dl.tui.app import DlApp
    from tests.test_app import FakeClient

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path / "state")
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"x" * 10)
    blob = tmp_path / "debian.iso.torrent"
    blob.write_bytes(b"d4:infoe")

    client = FakeClient()
    client.active = [{
        "gid": "child", "status": "active", "totalLength": "10", "completedLength": "5",
        "downloadSpeed": "1", "connections": "4", "dir": str(tmp_path),
        "files": [{"path": str(iso), "uris": []}],
        "bittorrent": {"mode": "single", "info": {"name": "debian.iso"}},
    }]
    client.tell_status = lambda gid: (
        {"gid": "child", "status": "complete", "following": "parent",
         "files": [{"path": str(iso), "uris": []}]}
        if gid == "child"
        else {"gid": "parent", "files": [{"path": str(blob), "uris": []}]}
    )

    app = DlApp(sandbox_cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("d")
        await pilot.pause()
        app.screen.query_one("#disk", Button).press()
        await pilot.pause()
        await pilot.pause()
    assert not blob.exists(), "the .torrent survived a delete from disk"


def test_a_magnet_goes_to_the_torrents_folder(sandbox_cfg):
    from dl import routing

    where = routing.resolve(MAGNET, "", sandbox_cfg)
    assert where.path == sandbox_cfg.categories["torrents"].dir
    assert where.category.name == "torrents"


def test_a_torrent_url_goes_to_the_torrents_folder(sandbox_cfg):
    from dl import routing

    where = routing.resolve("https://e.com/thing.torrent", "", sandbox_cfg)
    assert where.path == sandbox_cfg.categories["torrents"].dir


def test_a_local_torrent_goes_to_the_torrents_folder(tmp_path, sandbox_cfg):
    from dl import routing

    blob = tmp_path / "x.torrent"
    blob.write_bytes(b"d4:infoe")
    assert routing.resolve(str(blob), "", sandbox_cfg).path == sandbox_cfg.categories["torrents"].dir


def test_a_plain_download_is_unaffected_by_any_of_this(sandbox_cfg):
    from dl import routing

    assert routing.resolve("https://e.com/a.iso", "", sandbox_cfg).category.name == "iso"


def test_dash_d_still_beats_the_torrents_folder(tmp_path, sandbox_cfg):
    from dl import routing

    assert routing.resolve(MAGNET, "", sandbox_cfg, explicit_dir=tmp_path).path == tmp_path


def test_a_config_naming_a_torrents_category_uses_its_folder(tmp_path, sandbox_cfg):
    from dl import config, routing

    cats = dict(sandbox_cfg.categories)
    cats["torrents"] = config.Category("torrents", tmp_path / "Swarm", ("torrent",), "🧲", "#888888")
    cfg = config.replace(sandbox_cfg, categories=cats)
    assert routing.torrent_destination(cfg).path == tmp_path / "Swarm"


def test_a_config_without_one_still_gets_a_torrents_folder(sandbox_cfg):
    """Deleting a category is allowed, and a torrent still needs somewhere."""
    from dl import config, routing

    cats = {n: c for n, c in sandbox_cfg.categories.items() if n != "torrents"}
    cfg = config.replace(sandbox_cfg, categories=cats)
    assert routing.torrent_destination(cfg).path == cfg.general.default_dir / "Torrents"


def test_the_defaults_carry_a_torrents_category():
    from dl import config

    assert "torrents" in config.DEFAULT_CATEGORIES
    assert config.DEFAULT_CATEGORIES["torrents"].dir.name == "Torrents"
    assert "[categories.torrents]" in config.DEFAULT_TOML


def test_a_finished_torrent_is_not_moved_out_of_the_torrents_folder(tmp_path, sandbox_cfg):
    """It was asked to live there, whatever it turned out to contain."""
    from dl import config, hook

    where = tmp_path / "Torrents"
    where.mkdir()
    landed = where / "debian.iso"
    landed.write_bytes(b"x" * 10)
    cats = dict(sandbox_cfg.categories)
    cats["torrents"] = config.Category("torrents", where, ("torrent",), "🧲", "#888888")
    cfg = config.replace(sandbox_cfg, categories=cats)

    status = {"bittorrent": {"mode": "single", "info": {"name": "debian.iso"}}}
    assert hook.final_path(landed, cfg, "https://e.com/debian.iso", status) == landed
    assert landed.exists()


def test_a_finished_plain_download_still_moves(tmp_path, sandbox_cfg):
    from dl import config, hook

    cfg = config.replace(
        sandbox_cfg, general=config.replace(sandbox_cfg.general, default_dir=tmp_path)
    )
    landed = tmp_path / "debian.iso"
    landed.write_bytes(b"x" * 10)
    # A URL with no filename in it routes to the default folder, which is
    # where this is; the real name is what sends it on to iso.
    final = hook.final_path(landed, cfg, "https://e.com/get?id=7", {})
    assert final.parent == cfg.categories["iso"].dir


def test_a_torrent_row_is_labelled_a_torrent(sandbox_cfg):
    from dl.tui.table import row_from_status

    item = {
        "gid": "g1", "status": "active", "totalLength": "10", "completedLength": "1",
        "downloadSpeed": "0", "connections": "1", "dir": "/tmp/Torrents",
        "files": [{"path": "/tmp/Torrents/debian.iso", "uris": []}],
        "bittorrent": {"mode": "single", "info": {"name": "debian.iso"}},
    }
    assert row_from_status(item, sandbox_cfg).category.name == "torrents"
