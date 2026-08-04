import io

import pytest

from dl import cli, routing


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class FakeClient:
    def __init__(self):
        self.added = []
        self.paused = []
        self.unpaused = []
        self.removed = []
        self.global_options = {}
        self.shutdown_called = False
        self.active = []
        self.waiting = []

    def add_uri(self, uris, options):
        self.added.append((uris, options))
        return f"gid{len(self.added)}"

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return self.waiting

    def tell_stopped(self, offset=0, num=1000):
        return []

    def pause(self, gid):
        self.paused.append(gid)
        return gid

    def unpause(self, gid):
        self.unpaused.append(gid)
        return gid

    def remove(self, gid):
        self.removed.append(gid)

    def tell_status(self, gid):
        return {"gid": gid, "status": "removed" if gid in self.removed else "active"}
        return gid

    def change_global_option(self, options):
        self.global_options.update(options)
        return "OK"

    def shutdown(self):
        self.shutdown_called = True
        return "OK"


def test_add_options_carry_dir_and_limits(cfg):
    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution)
    assert options["dir"] == str(cfg.categories["iso"].dir)
    assert options["max-connection-per-server"] == "16"
    assert options["split"] == "16"
    assert options["min-split-size"] == "1M"


def test_add_options_omit_the_proxy_by_default(cfg):
    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    assert "all-proxy" not in cli.add_options(cfg, resolution)


def test_add_options_carry_the_proxy_when_asked(cfg):
    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution, proxy=True)
    assert options["all-proxy"] == "http://127.0.0.1:2080"


def test_cmd_add_sends_every_url_through_the_proxy(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None, proxy=True)
    assert all(opts.get("all-proxy") for _uris, opts in client.added)


def test_cmd_add_without_proxy_sends_nothing_extra(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None)
    assert all("all-proxy" not in opts for _uris, opts in client.added)


def test_add_options_rename_keeps_auto_renaming(cfg):
    from dl.duplicates import RENAME

    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution, decision=RENAME)
    assert options["auto-file-renaming"] == "true"
    assert options["allow-overwrite"] == "false"


def test_add_options_rename_turns_off_resume(cfg):
    """--continue resumes into the existing file instead of renaming, which
    destroys the very copy rename exists to preserve."""
    from dl.duplicates import RENAME

    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    assert cli.add_options(cfg, resolution, decision=RENAME)["continue"] == "false"


def test_add_options_leave_resume_alone_for_other_decisions(cfg):
    from dl.duplicates import OVERWRITE

    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    assert "continue" not in cli.add_options(cfg, resolution, decision=OVERWRITE)
    assert "continue" not in cli.add_options(cfg, resolution)


def test_add_options_overwrite_reuses_the_same_name(cfg):
    from dl.duplicates import OVERWRITE

    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution, decision=OVERWRITE)
    assert options["auto-file-renaming"] == "false"
    assert options["allow-overwrite"] == "true"


def test_add_options_without_a_decision_stay_out_of_the_way(cfg):
    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution)
    assert "auto-file-renaming" not in options
    assert "allow-overwrite" not in options


def test_cmd_add_skips_a_url_the_user_declined(cfg, capsys):
    from dl.duplicates import SKIP

    client = FakeClient()
    rc, gids = cli.cmd_add(
        ["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None, decisions=[SKIP, None]
    )
    assert rc == 0
    assert len(gids) == 1
    assert len(client.added) == 1
    assert "skipped" in capsys.readouterr().out


def test_cmd_add_skipping_everything_queues_nothing(cfg):
    from dl.duplicates import SKIP

    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, decisions=[SKIP])
    assert rc == 0
    assert gids == []
    assert client.added == []


def test_cmd_add_overwrite_deletes_the_file_it_replaces(cfg, tmp_path):
    from dl.duplicates import OVERWRITE

    victim = tmp_path / "a.iso"
    victim.write_text("old")
    (tmp_path / "a.iso.aria2").write_text("ctl")

    client = FakeClient()
    cli.cmd_add(
        ["https://e.com/a.iso"], cfg, client, tmp_path, decisions=[OVERWRITE]
    )
    assert not victim.exists()
    assert not (tmp_path / "a.iso.aria2").exists()
    assert len(client.added) == 1


def test_cmd_add_overwrite_evicts_an_unfinished_duplicate(cfg, tmp_path):
    from dl.duplicates import OVERWRITE

    victim = tmp_path / "a.iso"
    victim.write_text("partial")

    client = FakeClient()
    client.active = [
        {
            "gid": "gOld",
            "status": "active",
            "files": [{"path": str(victim), "uris": [{"uri": "https://e.com/a.iso"}]}],
        }
    ]
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, tmp_path, decisions=[OVERWRITE])
    assert client.removed == ["gOld"]
    assert not victim.exists()


def test_cmd_add_queues_each_url(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None)
    assert rc == 0
    assert len(client.added) == 2
    out = capsys.readouterr().out
    assert "a.iso" in out and "b.mkv" in out


def test_cmd_add_routes_by_extension(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None)
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)


def test_cmd_add_honours_explicit_dir(cfg, tmp_path):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, tmp_path)
    assert client.added[0][1]["dir"] == str(tmp_path)


def test_cmd_add_creates_destination_directory(cfg, tmp_path):
    client = FakeClient()
    target = tmp_path / "made" / "here"
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, target)
    assert target.is_dir()


def test_cmd_add_rejects_unwritable_destination(cfg, tmp_path, capsys):
    client = FakeClient()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, locked / "sub")
        assert rc == 1
        assert gids == []
        assert "cannot write" in capsys.readouterr().err
        assert not client.added
    finally:
        locked.chmod(0o700)


def test_cmd_add_with_no_urls_is_an_error(cfg, capsys):
    rc, gids = cli.cmd_add([], cfg, FakeClient(), None)
    assert rc == 1
    assert gids == []
    assert capsys.readouterr().err


@pytest.mark.parametrize(
    "value,ok",
    [
        ("https://e.com/a.iso", True),
        ("http://e.com/a.iso", True),
        ("ftp://e.com/a.iso", True),
        ("magnet:?xt=urn:btih:abc", True),
        ("/local/file.torrent", True),
        ("limit off", False),
        ("pause", False),
        ("lst", False),
        ("", False),
    ],
)
def test_looks_like_url(value, ok):
    assert cli.looks_like_url(value) is ok


def test_cmd_add_refuses_a_mistyped_subcommand_instead_of_downloading_it(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["limit off"], cfg, client, None)
    assert rc == 1
    assert gids == []
    err = capsys.readouterr().err
    assert "not a URL" in err
    assert "--help" in err
    assert not client.added


def test_cmd_add_rejects_the_whole_batch_if_any_entry_is_not_a_url(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso", "oops"], cfg, client, None)
    assert rc == 1
    assert gids == []
    assert not client.added


def test_cmd_add_returns_gids_in_argument_order(cfg):
    client = FakeClient()
    rc, gids = cli.cmd_add(
        ["https://e.com/a.iso", "https://e.com/b.mkv", "https://e.com/c.zip"], cfg, client, None
    )
    assert rc == 0
    assert gids == ["gid1", "gid2", "gid3"]


def test_cmd_add_skips_gids_for_unwritable_destinations(cfg, tmp_path, capsys):
    client = FakeClient()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, locked / "sub")
        assert (rc, gids) == (1, [])
    finally:
        locked.chmod(0o700)
    capsys.readouterr()


def test_cmd_ls_lists_active_and_waiting(cfg, capsys):
    client = FakeClient()
    client.active = [
        {"gid": "g1", "status": "active", "totalLength": "1000", "completedLength": "500",
         "downloadSpeed": "100", "files": [{"path": "/tmp/a.iso", "uris": []}]}
    ]
    client.waiting = [
        {"gid": "g2", "status": "waiting", "totalLength": "0", "completedLength": "0",
         "downloadSpeed": "0", "files": [{"path": "/tmp/b.mkv", "uris": []}]}
    ]
    assert cli.cmd_ls(cfg, client, use_color=False) == 0
    out = capsys.readouterr().out
    assert "g1" in out and "a.iso" in out and "50%" in out
    assert "g2" in out and "b.mkv" in out


def test_cmd_ls_emits_no_escape_codes_without_color(cfg, capsys):
    client = FakeClient()
    client.active = [
        {"gid": "g1", "status": "active", "totalLength": "100", "completedLength": "1",
         "downloadSpeed": "1", "files": [{"path": "/tmp/a.iso", "uris": []}]}
    ]
    cli.cmd_ls(cfg, client, use_color=False)
    assert "\x1b[" not in capsys.readouterr().out


def test_cmd_pause_single_gid():
    client = FakeClient()
    assert cli.cmd_pause("g1", client) == 0
    assert client.paused == ["g1"]


def test_cmd_pause_all_pauses_every_active():
    client = FakeClient()
    client.active = [{"gid": "g1"}, {"gid": "g2"}]
    cli.cmd_pause("all", client)
    assert client.paused == ["g1", "g2"]


def test_cmd_resume_all_unpauses_every_waiting():
    client = FakeClient()
    client.waiting = [{"gid": "g3"}]
    cli.cmd_resume("all", client)
    assert client.unpaused == ["g3"]


def test_cmd_rm_removes_gid():
    client = FakeClient()
    assert cli.cmd_rm("g9", client) == 0
    assert client.removed == ["g9"]




def test_cmd_kill_calls_shutdown():
    client = FakeClient()
    assert cli.cmd_kill(client) == 0
    assert client.shutdown_called


def test_read_url_file_from_disk(tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("https://e.com/a.iso\n\n# comment\nhttps://e.com/b.mkv\n")
    assert cli.read_url_file(str(p)) == ["https://e.com/a.iso", "https://e.com/b.mkv"]


def test_read_url_file_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("https://e.com/a.iso\n"))
    assert cli.read_url_file("-") == ["https://e.com/a.iso"]


def test_cmd_add_uses_a_chosen_directory(cfg, tmp_path):
    client = FakeClient()
    target = tmp_path / "picked"
    rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[target])
    assert rc == 0
    assert client.added[0][1]["dir"] == str(target)
    assert target.is_dir()


def test_cmd_add_chosen_none_entry_falls_back_to_routing(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[None])
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)


def test_cmd_add_chosen_applies_positionally(cfg, tmp_path):
    client = FakeClient()
    first = tmp_path / "one"
    cli.cmd_add(
        ["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None, chosen=[first, None]
    )
    assert client.added[0][1]["dir"] == str(first)
    assert client.added[1][1]["dir"] == str(cfg.categories["video"].dir)


def test_cmd_add_keeps_the_filetype_icon_for_a_chosen_directory(cfg, tmp_path, capsys):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[tmp_path / "picked"])
    assert "💿" in capsys.readouterr().out


def test_cmd_add_chosen_wins_over_explicit_dir(cfg, tmp_path):
    client = FakeClient()
    cli.cmd_add(
        ["https://e.com/a.iso"], cfg, client, tmp_path / "flag", chosen=[tmp_path / "picked"]
    )
    assert client.added[0][1]["dir"] == str(tmp_path / "picked")


def test_cmd_add_without_chosen_is_unchanged(cfg):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, None)
    assert rc == 0
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)
