"""One daemon, one dashboard.

Roaming to a free port when the preferred one was busy is how stray daemons
got their own ports and became invisible: nothing could reach them, and
nothing knew they were there.
"""

import os

import pytest

from dl import daemon, instance


def test_there_is_one_port_not_a_range():
    """A range is what let a second daemon quietly exist beside the first."""
    assert isinstance(daemon.PORT, int)
    assert not hasattr(daemon, "PORT_RANGE")


def test_the_pid_file_round_trips(tmp_path):
    daemon.write_pid(tmp_path, 4321)
    assert daemon.read_pid(tmp_path) == 4321


def test_a_missing_pid_file_reads_as_nothing(tmp_path):
    assert daemon.read_pid(tmp_path) == 0


def test_a_corrupt_pid_file_reads_as_nothing(tmp_path):
    (tmp_path / "daemon.pid").write_text("not a number")
    assert daemon.read_pid(tmp_path) == 0


def test_clearing_the_pid_file_removes_it(tmp_path):
    daemon.write_pid(tmp_path, 4321)
    daemon.clear_pid(tmp_path)
    assert daemon.read_pid(tmp_path) == 0


def test_clearing_a_pid_file_that_is_not_there_is_quiet(tmp_path):
    daemon.clear_pid(tmp_path)


def test_our_own_pid_counts_as_alive(tmp_path):
    assert daemon.alive(os.getpid()) is True


def test_pid_zero_is_never_alive(tmp_path):
    assert daemon.alive(0) is False


def test_a_pid_that_is_gone_is_not_alive():
    assert daemon.alive(999_999) is False


class Lock:
    """The dashboard lock, which is what stops a second `dl` window."""


def test_the_lock_is_taken_and_released(tmp_path):
    assert instance.acquire(tmp_path) is True
    assert instance.holder(tmp_path) == os.getpid()
    instance.release(tmp_path)
    assert instance.holder(tmp_path) == 0


def test_a_second_dashboard_is_refused(tmp_path):
    instance.acquire(tmp_path)
    other = instance.acquire(tmp_path, pid=os.getpid() + 1)
    assert other is False


def test_two_starting_together_do_not_both_get_in(tmp_path):
    """Read-then-write let both see an empty lock and both take it."""
    first = instance.acquire(tmp_path, pid=os.getpid())
    second = instance.acquire(tmp_path, pid=os.getpid() + 1)
    assert [first, second] == [True, False]
    assert instance.holder(tmp_path) == os.getpid()


def test_taking_the_lock_twice_from_the_same_process_is_fine(tmp_path):
    assert instance.acquire(tmp_path) is True
    assert instance.acquire(tmp_path) is True


def test_a_lock_left_by_a_dead_process_is_taken_over(tmp_path):
    """A crash must not lock the dashboard out for good."""
    instance.acquire(tmp_path, pid=999_999)
    assert instance.acquire(tmp_path) is True
    assert instance.holder(tmp_path) == os.getpid()


def test_a_corrupt_lock_is_taken_over(tmp_path):
    (tmp_path / "dl.lock").write_text("nonsense")
    assert instance.acquire(tmp_path) is True


def test_releasing_a_lock_we_do_not_hold_leaves_it_alone(tmp_path):
    instance.acquire(tmp_path, pid=os.getpid() + 1)
    instance.release(tmp_path)
    assert instance.holder(tmp_path) == os.getpid() + 1


def test_holder_of_a_dead_process_is_nothing(tmp_path):
    instance.acquire(tmp_path, pid=999_999)
    assert instance.holder(tmp_path) == 0


@pytest.fixture
def cfg_and_state(sandbox_cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda name: "/usr/bin/aria2c")
    return sandbox_cfg, tmp_path / "state"


def test_a_daemon_that_answers_is_adopted(cfg_and_state, monkeypatch):
    cfg, state = cfg_and_state
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "ours")
    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda *a: spawned.append(a))
    client = daemon.ensure_running(cfg, state)
    assert client.port == daemon.PORT
    assert spawned == []


def test_a_stale_daemon_of_ours_is_stopped_before_starting_a_new_one(
    cfg_and_state, monkeypatch
):
    """Same port, secret it no longer knows. It is ours, so it goes."""
    cfg, state = cfg_and_state
    daemon.write_pid(state, 4321)
    monkeypatch.setattr(daemon, "alive", lambda pid: pid == 4321)
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: True)
    stopped = []
    monkeypatch.setattr(daemon, "_terminate", lambda pid: stopped.append(pid))
    monkeypatch.setattr(daemon, "_spawn", lambda *a: None)
    monkeypatch.setattr(daemon, "_await_rpc", lambda *a: True)
    daemon.ensure_running(cfg, state)
    assert stopped == [4321]


def test_a_port_held_by_something_else_is_reported_not_worked_around(
    cfg_and_state, monkeypatch
):
    """Roaming to another port is what created invisible daemons."""
    cfg, state = cfg_and_state
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: False)
    monkeypatch.setattr(daemon, "read_pid", lambda state: 0)
    monkeypatch.setattr(daemon, "_wait_bindable", lambda port, timeout: False)
    with pytest.raises(daemon.DaemonStartFailed) as exc:
        daemon.ensure_running(cfg, state)
    assert str(daemon.PORT) in str(exc.value)


def test_a_port_still_letting_go_is_waited_for_not_declared_taken(
    cfg_and_state, monkeypatch
):
    """A daemon asked to stop holds its socket a moment longer. Failing on the
    first refused bind turns every migration into "someone else has the port"."""
    cfg, state = cfg_and_state
    attempts = []

    def slow_release(port):
        attempts.append(port)
        return len(attempts) > 3

    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", slow_release)
    monkeypatch.setattr(daemon, "_spawn", lambda *a: None)
    monkeypatch.setattr(daemon, "_await_rpc", lambda *a: True)
    monkeypatch.setattr(daemon.time, "sleep", lambda s: None)
    client = daemon.ensure_running(cfg, state)
    assert client.port == daemon.PORT
    assert len(attempts) > 1


def test_our_own_daemon_is_never_a_stray(cfg_and_state, monkeypatch):
    """An older version could leave ours on another port. It answers our
    secret, so it is retired properly rather than reported as a stranger."""
    cfg, state = cfg_and_state
    monkeypatch.setattr(daemon, "listening", lambda port: port in (6811, 6813))
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "ours" if port == 6811 else "free")
    assert daemon.strays(state) == [6813]


def test_nothing_listening_means_no_strays(cfg_and_state, monkeypatch):
    cfg, state = cfg_and_state
    monkeypatch.setattr(daemon, "listening", lambda port: False)
    assert daemon.strays(state) == []


def test_a_daemon_left_on_another_port_is_shut_down_on_the_next_start(
    cfg_and_state, monkeypatch
):
    """It holds the session, so asking it to stop brings its downloads back
    when the daemon restarts on the one port."""
    cfg, state = cfg_and_state
    daemon.write_port(state, 6813)
    asked = []

    class Stub:
        def __init__(self, host, port, secret, timeout=5.0):
            self.port = port

        def shutdown(self):
            asked.append(self.port)

    seen = {"6813": "ours"}
    monkeypatch.setattr(daemon, "Aria2", Stub)
    monkeypatch.setattr(
        daemon, "_probe", lambda port, secret: seen.pop(str(port), "free")
    )
    monkeypatch.setattr(daemon, "_bindable", lambda port: True)
    monkeypatch.setattr(daemon, "_spawn", lambda *a: None)
    monkeypatch.setattr(daemon, "_await_rpc", lambda *a: True)
    daemon.ensure_running(cfg, state)
    assert asked == [6813]


def test_starting_records_the_pid(cfg_and_state, monkeypatch):
    cfg, state = cfg_and_state
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: True)
    monkeypatch.setattr(daemon, "_spawn", lambda cfg, state, port, secret: daemon.write_pid(state, 777))
    monkeypatch.setattr(daemon, "_await_rpc", lambda *a: True)
    daemon.ensure_running(cfg, state)
    assert daemon.read_pid(state) == 777
