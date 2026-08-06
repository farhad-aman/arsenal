"""`dl ls ubuntu` and `dl history ubuntu`.

Both reuse the matching the dashboard uses, so a name that filters in the TUI
filters the same way on the command line.
"""

import pytest

from dl import cli, history


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def entry(gid, name, status="active"):
    return {
        "gid": gid,
        "status": status,
        "totalLength": "1000",
        "completedLength": "500",
        "downloadSpeed": "0",
        "files": [{"path": f"/tmp/{name}", "uris": [{"uri": f"https://e.com/{name}"}]}],
        "errorMessage": "",
    }


class Client:
    def __init__(self, active=()):
        self.active = list(active)

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return []

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_option(self, gid):
        return {}


def log_with(tmp_path, *names):
    path = tmp_path / "history.jsonl"
    for i, name in enumerate(names):
        history.append(
            {
                "ts": 1785942378 + i,
                "name": name,
                "bytes": 10,
                "path": f"/tmp/{name}",
                "category": "iso",
                "url": f"https://e.com/{name}",
                "status": "ok",
            },
            path,
        )
    return path


def test_ls_without_a_query_shows_everything(cfg, capsys):
    client = Client([entry("g1", "ubuntu.iso"), entry("g2", "debian.iso")])
    cli.cmd_ls(cfg, client, use_color=False)
    out = capsys.readouterr().out
    assert "ubuntu.iso" in out
    assert "debian.iso" in out


def test_ls_with_a_query_keeps_only_matches(cfg, capsys):
    client = Client([entry("g1", "ubuntu.iso"), entry("g2", "debian.iso")])
    cli.cmd_ls(cfg, client, use_color=False, query="ubuntu")
    out = capsys.readouterr().out
    assert "ubuntu.iso" in out
    assert "debian.iso" not in out


def test_ls_matching_ignores_case(cfg, capsys):
    client = Client([entry("g1", "Ubuntu.ISO")])
    cli.cmd_ls(cfg, client, use_color=False, query="ubuntu")
    assert "Ubuntu.ISO" in capsys.readouterr().out


def test_ls_with_no_match_prints_nothing(cfg, capsys):
    client = Client([entry("g1", "ubuntu.iso")])
    cli.cmd_ls(cfg, client, use_color=False, query="nothing")
    assert capsys.readouterr().out.strip() == ""


def test_history_with_a_query_keeps_only_matches(cfg, tmp_path, capsys):
    log = log_with(tmp_path, "ubuntu.iso", "debian.iso", "clip.mp4")
    cli.cmd_history(cfg, log, ["ubuntu"])
    out = capsys.readouterr().out
    assert "ubuntu.iso" in out
    assert "debian.iso" not in out


def test_history_takes_a_count_and_a_query_together(cfg, tmp_path, capsys):
    log = log_with(tmp_path, *[f"ubuntu-{i}.iso" for i in range(10)])
    cli.cmd_history(cfg, log, ["3", "ubuntu"])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert len(lines) == 3


def test_history_searches_past_the_default_count(cfg, tmp_path, capsys):
    """The point of the query: an old download the default 20 would never reach."""
    names = ["ubuntu-ancient.iso"] + [f"filler-{i}.bin" for i in range(200)]
    log = log_with(tmp_path, *names)
    cli.cmd_history(cfg, log, ["ancient"])
    assert "ubuntu-ancient.iso" in capsys.readouterr().out


def test_history_query_matches_unicode_the_way_search_does(cfg, tmp_path, capsys):
    import unicodedata

    log = log_with(tmp_path, unicodedata.normalize("NFD", "قهرمان.mkv"))
    cli.cmd_history(cfg, log, [unicodedata.normalize("NFC", "قهرمان")])
    assert "قهرمان" in capsys.readouterr().out


def test_history_with_no_match_says_so(cfg, tmp_path, capsys):
    log = log_with(tmp_path, "ubuntu.iso")
    cli.cmd_history(cfg, log, ["nothing"])
    assert "nothing found" in capsys.readouterr().out.lower()


def test_history_query_combines_with_failed(cfg, tmp_path, capsys):
    log = tmp_path / "history.jsonl"
    history.append({"ts": 1, "name": "ubuntu.iso", "status": "ok", "bytes": 1}, log)
    history.append(
        {"ts": 2, "name": "ubuntu-bad.iso", "status": "error", "bytes": 0, "error": "403"}, log
    )
    cli.cmd_history(cfg, log, ["ubuntu", "--failed"])
    out = capsys.readouterr().out
    assert "ubuntu-bad.iso" in out
    assert "\nubuntu.iso" not in out


def test_history_still_rejects_two_counts(cfg, tmp_path, capsys):
    """A second number is a mistake, not a query."""
    log = log_with(tmp_path, "ubuntu.iso")
    assert cli.cmd_history(cfg, log, ["5", "10"]) == 1


def test_history_json_output_respects_the_query(cfg, tmp_path, capsys):
    log = log_with(tmp_path, "ubuntu.iso", "debian.iso")
    cli.cmd_history(cfg, log, ["ubuntu", "--json"])
    out = capsys.readouterr().out
    assert "ubuntu.iso" in out
    assert "debian.iso" not in out
