"""Finding aria2 daemons dl cannot reach.

Scanning the port range older versions could roam to only finds them there.
Looking for the processes themselves finds them wherever they ended up —
including a daemon left by a version that used a different range.
"""

import subprocess

import pytest

from dl import daemon

PS = (
    "2236 aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port=6813 --rpc-secret=x\n"
    "63710 aria2c --enable-rpc --rpc-listen-all=false --rpc-listen-port=6810 --rpc-secret=y\n"
)


def listing(monkeypatch, text=PS, code=0):
    monkeypatch.setattr(
        daemon.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, code, stdout=text, stderr=""),
    )


def test_running_daemons_are_found_with_their_ports(monkeypatch):
    listing(monkeypatch)
    assert daemon.aria2_processes() == [(2236, 6813), (63710, 6810)]


def test_nothing_running_is_no_daemons(monkeypatch):
    listing(monkeypatch, text="", code=1)
    assert daemon.aria2_processes() == []


def test_a_process_without_a_port_is_ignored(monkeypatch):
    """aria2 truncates its own process title, so a line can arrive without
    the argument we need."""
    listing(monkeypatch, text="999 aria2c --enable-rpc\n")
    assert daemon.aria2_processes() == []


def test_a_line_that_makes_no_sense_is_ignored(monkeypatch):
    listing(monkeypatch, text="not a process line\n")
    assert daemon.aria2_processes() == []


def test_ps_being_unavailable_is_not_an_error(monkeypatch):
    def explode(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(daemon.subprocess, "run", explode)
    assert daemon.aria2_processes() == []


def test_our_own_daemon_is_not_a_stray(tmp_path, monkeypatch):
    listing(monkeypatch)
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "ours" if port == 6810 else "free")
    assert daemon.strays(tmp_path) == [(2236, 6813)]


def test_a_daemon_outside_the_old_range_is_still_found(tmp_path, monkeypatch):
    """The whole point: a port range only finds what was inside it."""
    listing(
        monkeypatch,
        text="4242 aria2c --enable-rpc --rpc-listen-port=9999 --rpc-secret=z\n",
    )
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    assert daemon.strays(tmp_path) == [(4242, 9999)]


def test_every_daemon_answering_us_leaves_no_strays(tmp_path, monkeypatch):
    listing(monkeypatch)
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "ours")
    assert daemon.strays(tmp_path) == []


def test_stopping_strays_terminates_each_pid(monkeypatch):
    stopped = []
    monkeypatch.setattr(daemon, "_terminate", lambda pid: stopped.append(pid))
    assert daemon.stop_strays([(2236, 6813), (4242, 9999)]) == 2
    assert stopped == [2236, 4242]


def test_stopping_nothing_stops_nothing(monkeypatch):
    monkeypatch.setattr(daemon, "_terminate", lambda pid: pytest.fail("stopped something"))
    assert daemon.stop_strays([]) == 0
