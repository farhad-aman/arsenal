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


def test_ctrl_c_returns_130(monkeypatch):
    def boom(_a):
        raise KeyboardInterrupt

    monkeypatch.setattr(entry, "_run", boom)
    assert entry.main(["ls"]) == 130
