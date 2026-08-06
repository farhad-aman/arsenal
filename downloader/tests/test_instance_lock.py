"""A second dashboard is refused rather than opened beside the first."""

import os

import pytest

from dl import instance
from dl.tui import app as app_module
from tests.test_app import FakeClient


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def test_the_dashboard_takes_the_lock_and_gives_it_back(cfg, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(app_module, "DlApp", lambda *a: type("X", (), {"run": lambda s: ran.append(instance.holder(tmp_path))})())
    assert app_module.run_tui(cfg, FakeClient(), tmp_path) == 0
    assert ran == [os.getpid()]
    assert instance.holder(tmp_path) == 0


def test_a_second_dashboard_is_refused(cfg, tmp_path, monkeypatch, capsys):
    instance.acquire(tmp_path, pid=os.getpid() + 1)
    started = []
    monkeypatch.setattr(app_module, "DlApp", lambda *a: started.append(1))
    assert app_module.run_tui(cfg, FakeClient(), tmp_path) == 1
    assert started == []
    assert "already running" in capsys.readouterr().err


def test_the_lock_is_released_even_if_the_dashboard_raises(cfg, tmp_path, monkeypatch):
    class Boom:
        def run(self):
            raise RuntimeError("crash")

    monkeypatch.setattr(app_module, "DlApp", lambda *a: Boom())
    with pytest.raises(RuntimeError):
        app_module.run_tui(cfg, FakeClient(), tmp_path)
    assert instance.holder(tmp_path) == 0


def test_a_lock_from_a_dead_process_does_not_shut_us_out(cfg, tmp_path, monkeypatch):
    instance.acquire(tmp_path, pid=999_999)
    monkeypatch.setattr(app_module, "DlApp", lambda *a: type("X", (), {"run": lambda s: None})())
    assert app_module.run_tui(cfg, FakeClient(), tmp_path) == 0
