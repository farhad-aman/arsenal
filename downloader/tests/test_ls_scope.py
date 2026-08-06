"""What `dl ls` shows, and what a removal leaves behind.

aria2.remove does not erase a download: it moves it to the stopped list with
status "removed", where it stays until the daemon restarts. Printing the
stopped list unfiltered meant every deletion stayed on screen for the life of
the daemon.
"""

import pytest

from dl import cli


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def entry(gid, status, name):
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
    def __init__(self, active=(), waiting=(), stopped=()):
        self.active = list(active)
        self.waiting = list(waiting)
        self.stopped = list(stopped)
        self.removed = []
        self.purged = []

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return self.waiting

    def tell_stopped(self, offset=0, num=1000):
        return self.stopped

    def get_option(self, gid):
        return {}

    def remove(self, gid):
        self.removed.append(gid)
        return gid

    def remove_download_result(self, gid):
        self.purged.append(gid)
        return "OK"


def test_a_removed_download_is_not_listed(cfg, capsys):
    client = Client(stopped=[entry("g1", "removed", "gone.iso")])
    cli.cmd_ls(cfg, client, use_color=False)
    assert "gone.iso" not in capsys.readouterr().out


def test_a_finished_download_is_not_listed(cfg, capsys):
    """It is in dl history, which is the lasting record."""
    client = Client(stopped=[entry("g1", "complete", "done.iso")])
    cli.cmd_ls(cfg, client, use_color=False)
    assert "done.iso" not in capsys.readouterr().out


def test_a_failed_download_is_still_listed(cfg, capsys):
    """A failure needs a decision, so it stays in front of you."""
    client = Client(stopped=[entry("g1", "error", "broken.iso")])
    cli.cmd_ls(cfg, client, use_color=False)
    assert "broken.iso" in capsys.readouterr().out


def test_active_and_waiting_are_listed(cfg, capsys):
    client = Client(
        active=[entry("g1", "active", "now.iso")],
        waiting=[entry("g2", "waiting", "next.iso")],
    )
    cli.cmd_ls(cfg, client, use_color=False)
    out = capsys.readouterr().out
    assert "now.iso" in out
    assert "next.iso" in out


def test_a_paused_download_is_listed(cfg, capsys):
    client = Client(waiting=[entry("g1", "paused", "held.iso")])
    cli.cmd_ls(cfg, client, use_color=False)
    assert "held.iso" in capsys.readouterr().out


def test_the_graveyard_does_not_crowd_out_the_queue(cfg, capsys):
    client = Client(
        active=[entry("a1", "active", "now.iso")],
        stopped=[entry(f"g{i}", "removed", f"old{i}.iso") for i in range(36)],
    )
    cli.cmd_ls(cfg, client, use_color=False)
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == 1
    assert "now.iso" in lines[0]


def test_removing_also_purges_the_result(cfg):
    """Without the purge the download comes straight back as "removed"."""
    client = Client(active=[entry("g1", "active", "now.iso")])
    cli.cmd_rm("g1", client)
    assert client.removed == ["g1"]
    assert client.purged == ["g1"]


def test_removing_reports_success_even_if_the_purge_is_refused(cfg):
    """aria2 refuses to purge a result that is not there yet. The download is
    still gone, which is what was asked for."""
    from dl.rpc import Aria2Error

    class Fussy(Client):
        def remove_download_result(self, gid):
            raise Aria2Error(1, "not found")

    client = Fussy(active=[entry("g1", "active", "now.iso")])
    assert cli.cmd_rm("g1", client) == 0
    assert client.removed == ["g1"]


def test_removing_survives_a_client_without_the_purge_call(cfg):
    """An older daemon, or a stub in a test, may not have it."""

    class Old(Client):
        remove_download_result = None

        def __getattr__(self, name):
            raise AttributeError(name)

    client = Old(active=[entry("g1", "active", "now.iso")])
    assert cli.cmd_rm("g1", client) == 0
