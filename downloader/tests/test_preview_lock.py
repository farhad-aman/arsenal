"""The preview is a dashboard too, and must not open beside a real one."""

import os

import pytest

from dl import instance
from dl.tui import preview as preview_module
from tests.test_app import FakeClient


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def test_the_preview_stands_down_while_a_dashboard_is_open(cfg, tmp_path, monkeypatch):
    instance.acquire(tmp_path, pid=os.getpid() + 1)
    started = []
    monkeypatch.setattr(preview_module, "PreviewApp", lambda *a: started.append(1))
    lines, cancelled = preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert started == []
    assert cancelled is False
    assert "queued" in lines[0]


def test_standing_down_does_not_undo_the_download(cfg, tmp_path, monkeypatch):
    """The URL is already with aria2 by this point. Refusing here would only
    hide it, not stop it."""
    instance.acquire(tmp_path, pid=os.getpid() + 1)
    lines, cancelled = preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert cancelled is False


def test_the_preview_runs_when_nothing_holds_the_lock(cfg, tmp_path, monkeypatch):
    ran = []

    class Stub:
        cancelled = False
        results = []

        def run(self):
            ran.append(instance.holder(tmp_path))

    monkeypatch.setattr(preview_module, "PreviewApp", lambda *a: Stub())
    preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert ran == [os.getpid()]


def test_the_preview_gives_the_lock_back(cfg, tmp_path, monkeypatch):
    class Stub:
        cancelled = False
        results = []

        def run(self):
            pass

    monkeypatch.setattr(preview_module, "PreviewApp", lambda *a: Stub())
    preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert instance.holder(tmp_path) == 0


def test_the_lock_is_given_back_even_if_the_preview_raises(cfg, tmp_path, monkeypatch):
    class Boom:
        def run(self):
            raise RuntimeError("crash")

    monkeypatch.setattr(preview_module, "PreviewApp", lambda *a: Boom())
    with pytest.raises(RuntimeError):
        preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert instance.holder(tmp_path) == 0


def test_another_process_cannot_start_a_dashboard_while_a_preview_is_up(
    cfg, tmp_path, monkeypatch
):
    """One lock, both doors. Reentrance from the same pid is deliberate — a
    preview and a dashboard are always separate processes."""
    refused = []

    class Stub:
        cancelled = False
        results = []

        def run(self):
            refused.append(instance.acquire(tmp_path, pid=os.getpid() + 1))

    monkeypatch.setattr(preview_module, "PreviewApp", lambda *a: Stub())
    preview_module.run_preview(cfg, FakeClient(), state=tmp_path)
    assert refused == [False]


def test_the_youtube_flow_refuses_while_a_dashboard_is_open(cfg, tmp_path, monkeypatch):
    """It opens a full screen and nothing is queued yet, so it points at the
    window that can add it rather than standing down silently."""
    from dl.tui import ytflow

    instance.acquire(tmp_path, pid=os.getpid() + 1)
    monkeypatch.setattr(ytflow, "_run_youtube", lambda *a: pytest.fail("opened anyway"))
    lines, cancelled = ytflow.run_youtube(cfg, ["https://youtu.be/abc"], state=tmp_path)
    assert cancelled is True
    assert "already running" in lines[0]


def test_the_youtube_flow_runs_when_nothing_holds_the_lock(cfg, tmp_path, monkeypatch):
    from dl.tui import ytflow

    monkeypatch.setattr(ytflow, "_run_youtube", lambda *a: (["ok"], False))
    lines, cancelled = ytflow.run_youtube(cfg, ["https://youtu.be/abc"], state=tmp_path)
    assert (lines, cancelled) == (["ok"], False)
    assert instance.holder(tmp_path) == 0


def test_the_youtube_flow_gives_the_lock_back_after_a_crash(cfg, tmp_path, monkeypatch):
    from dl.tui import ytflow

    def boom(*a):
        raise RuntimeError("crash")

    monkeypatch.setattr(ytflow, "_run_youtube", boom)
    with pytest.raises(RuntimeError):
        ytflow.run_youtube(cfg, ["https://youtu.be/abc"], state=tmp_path)
    assert instance.holder(tmp_path) == 0
