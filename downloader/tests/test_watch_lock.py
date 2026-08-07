"""The clipboard watcher counts as a copy of dl, not one of its commands.

It has no window, but it runs until stopped and queues downloads while it
does — two of those, or one beside a dashboard, is two copies acting on the
same queue.
"""

import os

import pytest

from dl import instance, watch


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class Client:
    def __init__(self):
        self.added = []

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        return "g1"

    def tell_active(self):
        return []

    def tell_waiting(self, offset=0, num=1000):
        return []

    def tell_stopped(self, offset=0, num=1000):
        return []


def test_watching_takes_the_lock(cfg, tmp_path):
    held = []
    watch.run(
        cfg,
        Client(),
        interval=0,
        reader=lambda: held.append(instance.holder(tmp_path)) or "",
        iterations=1,
        state=tmp_path,
    )
    assert held == [os.getpid()]


def test_watching_gives_the_lock_back(cfg, tmp_path):
    watch.run(cfg, Client(), interval=0, reader=lambda: "", iterations=1, state=tmp_path)
    assert instance.holder(tmp_path) == 0


def test_a_second_watcher_is_refused(cfg, tmp_path, capsys, other_pid):
    instance.acquire(tmp_path, pid=other_pid)
    polled = []
    assert (
        watch.run(
            cfg,
            Client(),
            interval=0,
            reader=lambda: polled.append(1) or "",
            iterations=1,
            state=tmp_path,
        )
        == 1
    )
    assert polled == []
    assert "already running" in capsys.readouterr().err


def test_watching_is_refused_while_a_dashboard_is_open(cfg, tmp_path, capsys, other_pid):
    """One lock, so it does not matter which door was used first."""
    instance.acquire(tmp_path, pid=other_pid)
    assert watch.run(cfg, Client(), interval=0, reader=lambda: "", iterations=1, state=tmp_path) == 1


def test_another_process_cannot_start_anything_while_watching(cfg, tmp_path, other_pid):
    """One lock, so it does not matter which door was used first. Reentrance
    from the same pid is deliberate — these are always separate processes."""
    refused = []

    def poll():
        refused.append(instance.acquire(tmp_path, pid=other_pid))
        return ""

    watch.run(cfg, Client(), interval=0, reader=poll, iterations=1, state=tmp_path)
    assert refused == [False]


def test_the_lock_is_given_back_after_a_crash(cfg, tmp_path):
    def explode():
        raise RuntimeError("clipboard on fire")

    with pytest.raises(RuntimeError):
        watch.run(cfg, Client(), interval=0, reader=explode, iterations=1, state=tmp_path)
    assert instance.holder(tmp_path) == 0


def test_stopping_with_ctrl_c_still_releases(cfg, tmp_path):
    def interrupt():
        raise KeyboardInterrupt

    assert watch.run(
        cfg, Client(), interval=0, reader=interrupt, iterations=1, state=tmp_path
    ) == 0
    assert instance.holder(tmp_path) == 0
