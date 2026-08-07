"""`dl ls --json`, to match `dl history --json`.

One object per line, so it pipes into jq a record at a time and stays greppable
the way the plain listing is.
"""

import json

import pytest

from dl import cli, config


@pytest.fixture
def cfg():
    return config.defaults()


def item(**over) -> dict:
    base = {
        "gid": "2089b05e",
        "status": "active",
        "totalLength": "1000",
        "completedLength": "700",
        "downloadSpeed": "150",
        "connections": "8",
        "files": [{"path": "/tmp/ISO/ubuntu.iso", "uris": [{"uri": "https://e.com/ubuntu.iso"}]}],
    }
    base.update(over)
    return base


class Client:
    def __init__(self, active=(), waiting=(), stopped=(), options=None):
        self._active, self._waiting, self._stopped = list(active), list(waiting), list(stopped)
        self._options = options or {}

    def tell_active(self):
        return self._active

    def tell_waiting(self):
        return self._waiting

    def tell_stopped(self):
        return self._stopped

    def get_option(self, gid):
        return self._options.get(gid, {})


def emitted(capsys) -> list[dict]:
    out = capsys.readouterr().out.strip()
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def test_each_download_is_one_json_object(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item(), item(gid="b")]), use_color=False, as_json=True)
    assert len(emitted(capsys)) == 2


def test_the_object_carries_what_the_listing_shows(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item()]), use_color=False, as_json=True)
    row = emitted(capsys)[0]
    assert row["gid"] == "2089b05e"
    assert row["status"] == "active"
    assert row["name"] == "ubuntu.iso"
    assert row["total"] == 1000
    assert row["completed"] == 700
    assert row["speed"] == 150
    assert row["percent"] == 70


def test_the_object_carries_what_the_listing_cannot(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item()]), use_color=False, as_json=True)
    row = emitted(capsys)[0]
    assert row["path"] == "/tmp/ISO/ubuntu.iso"
    assert row["url"] == "https://e.com/ubuntu.iso"
    assert row["category"] == "iso"
    assert row["connections"] == 8


def test_a_proxied_download_says_so(cfg, capsys):
    client = Client(active=[item()], options={"2089b05e": {"all-proxy": "http://127.0.0.1:2080"}})
    cli.cmd_ls(cfg, client, use_color=False, as_json=True)
    assert emitted(capsys)[0]["proxy"] is True


def test_a_direct_download_says_so(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item()]), use_color=False, as_json=True)
    assert emitted(capsys)[0]["proxy"] is False


def test_an_unknown_total_gives_zero_percent_rather_than_dividing_by_it(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item(totalLength="0")]), use_color=False, as_json=True)
    assert emitted(capsys)[0]["percent"] == 0


def test_a_multi_file_torrent_is_named_for_the_torrent(cfg, capsys):
    torrent_item = item(
        files=[{"path": "/tmp/D/Album/t1.flac", "uris": []}],
        bittorrent={"mode": "multi", "info": {"name": "Album"}},
        dir="/tmp/D",
    )
    cli.cmd_ls(cfg, Client(active=[torrent_item]), use_color=False, as_json=True)
    assert emitted(capsys)[0]["name"] == "Album"


def test_a_query_still_filters(cfg, capsys):
    client = Client(active=[item(), item(gid="b", files=[{"path": "/tmp/other.mkv"}])])
    cli.cmd_ls(cfg, client, use_color=False, query="ubuntu", as_json=True)
    rows = emitted(capsys)
    assert len(rows) == 1 and rows[0]["name"] == "ubuntu.iso"


def test_an_empty_queue_emits_nothing(cfg, capsys):
    cli.cmd_ls(cfg, Client(), use_color=False, as_json=True)
    assert capsys.readouterr().out.strip() == ""


def test_every_line_parses_on_its_own(cfg, capsys):
    """One object per line, not one array — so it streams into jq."""
    cli.cmd_ls(cfg, Client(active=[item(), item(gid="b")]), use_color=False, as_json=True)
    for line in capsys.readouterr().out.strip().splitlines():
        assert isinstance(json.loads(line), dict)


def test_a_persian_name_is_not_escaped(cfg, capsys):
    named = item(files=[{"path": "/tmp/فیلم.mkv", "uris": []}])
    cli.cmd_ls(cfg, Client(active=[named]), use_color=False, as_json=True)
    assert "فیلم.mkv" in capsys.readouterr().out


def test_the_plain_listing_is_unchanged(cfg, capsys):
    cli.cmd_ls(cfg, Client(active=[item()]), use_color=False)
    out = capsys.readouterr().out
    assert "2089b05e" in out and "ubuntu.iso" in out
    assert not out.strip().startswith("{")


def test_ls_accepts_the_json_flag_on_the_command_line():
    from dl import __main__ as entry

    assert "--json" in entry.SUBCOMMAND_FLAGS["ls"]
