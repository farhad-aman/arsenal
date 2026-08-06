import subprocess

from dl.tui import modals


def test_writing_puts_the_text_on_the_clipboard(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        seen["input"] = kwargs.get("input")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(modals.subprocess, "run", fake_run)
    assert modals.write_clipboard("https://e.com/a.iso") is True
    assert seen["argv"] == ["pbcopy"]
    assert seen["input"] == "https://e.com/a.iso"


def test_writing_nothing_is_refused(monkeypatch):
    """Silently clearing the clipboard is worse than doing nothing."""
    called = []
    monkeypatch.setattr(modals.subprocess, "run", lambda *a, **k: called.append(a))
    assert modals.write_clipboard("") is False
    assert called == []


def test_a_missing_pbcopy_is_reported_not_raised(monkeypatch):
    def explode(*a, **k):
        raise OSError("no pbcopy")

    monkeypatch.setattr(modals.subprocess, "run", explode)
    assert modals.write_clipboard("https://e.com/a.iso") is False


def test_a_timeout_is_reported_not_raised(monkeypatch):
    def explode(*a, **k):
        raise subprocess.TimeoutExpired("pbcopy", 2)

    monkeypatch.setattr(modals.subprocess, "run", explode)
    assert modals.write_clipboard("https://e.com/a.iso") is False


def test_a_nonzero_exit_is_a_failure(monkeypatch):
    monkeypatch.setattr(
        modals.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1)
    )
    assert modals.write_clipboard("https://e.com/a.iso") is False
