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


def test_a_finished_torrent_moves_into_its_category(tmp_path, sandbox_cfg):
    """The destination was picked from the .torrent's own filename, before
    anyone knew what was inside. Routing can only happen once it is known."""
    from dl import config, hook

    cfg = config.replace(sandbox_cfg, general=config.replace(sandbox_cfg.general, default_dir=tmp_path))
    landed = _complete(tmp_path)
    final = hook.relocate(landed, cfg, "https://e.com/debian.iso", by_content=True)
    assert final.parent == cfg.categories["iso"].dir
    assert final.exists() and not landed.exists()


def test_a_torrent_pinned_with_dash_d_stays_where_it_was_put(tmp_path, sandbox_cfg):
    from dl import config, hook

    pinned = tmp_path / "elsewhere"
    cfg = config.replace(sandbox_cfg, general=config.replace(sandbox_cfg.general, default_dir=tmp_path))
    landed = _complete(tmp_path, where=pinned)
    final = hook.relocate(landed, cfg, "https://e.com/debian.iso", by_content=True)
    assert final == landed and landed.exists()


def test_a_torrent_folder_with_nothing_to_route_on_stays_put(tmp_path, sandbox_cfg):
    from dl import config, hook

    cfg = config.replace(sandbox_cfg, general=config.replace(sandbox_cfg.general, default_dir=tmp_path))
    folder = tmp_path / "Some Album"
    folder.mkdir()
    final = hook.relocate(folder, cfg, "", by_content=True)
    assert final == folder and folder.exists()


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

    assert daemon.restart_if_stale(cfg, tmp_path, "s", Idle()) is False
    assert restarted == []


def test_a_daemon_started_before_a_setting_existed_is_restarted(tmp_path, monkeypatch):
    """The DHT cannot be turned on over RPC, so a daemon that predates it never
    gets it however long dl runs."""
    from dl import config, daemon

    daemon.write_signature(tmp_path, "from-an-older-dl")
    daemon.write_pid(tmp_path, 4242)
    restarted = []
    monkeypatch.setattr(daemon, "_terminate", lambda pid, wait=5.0: None)
    monkeypatch.setattr(daemon, "_wait_bindable", lambda port, timeout: True)
    monkeypatch.setattr(daemon, "_spawn", lambda *a: restarted.append(1))
    monkeypatch.setattr(daemon, "_await_rpc", lambda *a: True)

    class Idle:
        def tell_active(self):
            return []

        def tell_waiting(self):
            return []

    assert daemon.restart_if_stale(config.defaults(), tmp_path, "s", Idle()) is True
    assert restarted == [1]


def test_a_busy_daemon_is_never_restarted_under_a_download(tmp_path, monkeypatch):
    from dl import config, daemon

    daemon.write_signature(tmp_path, "from-an-older-dl")
    restarted = []
    monkeypatch.setattr(daemon, "_spawn", lambda *a: restarted.append(1))

    class Busy:
        def tell_active(self):
            return [{"gid": "g1"}]

        def tell_waiting(self):
            return []

    assert daemon.restart_if_stale(config.defaults(), tmp_path, "s", Busy()) is False
    assert restarted == []


def test_an_unreachable_daemon_is_not_restarted_from_here(tmp_path, monkeypatch):
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

    assert daemon.restart_if_stale(config.defaults(), tmp_path, "s", Gone()) is False
    assert restarted == []


def test_spawning_records_the_signature(tmp_path, monkeypatch):
    from dl import config, daemon

    monkeypatch.setattr(daemon.subprocess, "Popen", lambda *a, **k: type("P", (), {"pid": 7})())
    cfg = config.defaults()
    daemon._spawn(cfg, tmp_path, daemon.PORT, "s")
    wanted = daemon.args_signature(daemon.aria2_args(cfg, tmp_path, daemon.PORT, "s"))
    assert daemon.read_signature(tmp_path) == wanted
