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
