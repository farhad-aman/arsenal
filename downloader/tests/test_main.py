import pytest

from dl import __main__ as entry
from dl.rpc import Aria2Error, Aria2Unreachable


def test_help_exits_clean(capsys):
    assert entry.main(["--help"]) == 0
    assert "download manager" in capsys.readouterr().out


def test_unreachable_daemon_prints_a_message_not_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr(entry, "_run", lambda _a: (_ for _ in ()).throw(Aria2Unreachable("boom")))
    assert entry.main(["ls"]) == 1
    err = capsys.readouterr().err
    assert "dl: boom" in err
    assert "Traceback" not in err


def test_rpc_fault_prints_a_message_not_a_traceback(monkeypatch, capsys):
    def boom(_a):
        raise Aria2Error(1, "Unauthorized")

    monkeypatch.setattr(entry, "_run", boom)
    assert entry.main(["ls"]) == 1
    err = capsys.readouterr().err
    assert "Unauthorized" in err
    assert "Traceback" not in err


class StubClient:
    def add_uri(self, uris, options):
        return "gidX"


def _wire(monkeypatch, tmp_path, isatty, calls):
    from dl import cli, config, daemon

    monkeypatch.setattr(entry, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "ensure_running", lambda *a, **k: StubClient())
    monkeypatch.setattr(daemon, "bump_generation", lambda *a, **k: 1)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (0, ["gidX"]))
    monkeypatch.setattr("sys.stdout.isatty", lambda: isatty)
    monkeypatch.setattr(entry, "run_preview", lambda *a, **k: calls.append(a) or ["  done"])


def test_url_with_a_tty_attaches_the_preview(monkeypatch, tmp_path, capsys):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert len(calls) == 1
    assert "done" in capsys.readouterr().out


def test_url_without_a_tty_does_not_attach(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, False, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert calls == []


def test_no_preview_flag_suppresses_attachment(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["--no-preview", "https://e.com/a.iso"]) == 0
    assert calls == []


def test_no_preview_flag_is_not_treated_as_a_url(monkeypatch, tmp_path):
    from dl import cli

    seen = []
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)

    def record(urls, *args, **kwargs):
        seen.append(urls)
        return 0, ["gidX"]

    monkeypatch.setattr(cli, "cmd_add", record)
    entry.main(["--no-preview", "https://e.com/a.iso"])
    assert seen == [["https://e.com/a.iso"]]


def test_failed_add_returns_one_without_attaching(monkeypatch, tmp_path):
    from dl import cli

    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (1, []))
    assert entry.main(["oops"]) == 1
    assert calls == []


def test_partial_add_still_attaches_for_the_successes(monkeypatch, tmp_path):
    from dl import cli

    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (1, ["gidX"]))
    assert entry.main(["https://e.com/a.iso", "https://e.com/b.iso"]) == 1
    assert len(calls) == 1


def test_ctrl_c_returns_130(monkeypatch):
    def boom(_a):
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "_run", boom)
    assert entry.main(["ls"]) == 130
