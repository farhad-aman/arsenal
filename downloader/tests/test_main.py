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

    def fake_preview(cfg, client, gids=(), pending=(), queue=None, pick_paths=True):
        calls.append({"gids": gids, "pending": pending, "queue": queue, "pick_paths": pick_paths})
        if queue is not None:
            queue([None] * len(pending))
        return ["  done"], False

    monkeypatch.setattr(entry, "run_preview", fake_preview)


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


def test_interactive_run_goes_through_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert calls, "run_preview was not called"
    assert calls[0].get("pending"), "no pending requests were passed"
    assert calls[0].get("queue") is not None


def test_proxy_flag_reaches_cmd_add(monkeypatch, tmp_path):
    from dl import cli

    seen = {}
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)

    def record(urls, cfg, client, explicit_dir, chosen=None, proxy=False, decisions=None):
        seen["urls"] = urls
        seen["proxy"] = proxy
        return 0, ["gidX"]

    monkeypatch.setattr(cli, "cmd_add", record)
    entry.main(["-p", "https://e.com/a.iso"])
    assert seen["proxy"] is True
    assert seen["urls"] == ["https://e.com/a.iso"]


def _record_add(monkeypatch, seen):
    from dl import cli

    def record(urls, cfg, client, explicit_dir, chosen=None, proxy=False, decisions=None):
        seen["urls"] = urls
        seen["proxy"] = proxy
        return 0, ["gidX"]

    monkeypatch.setattr(cli, "cmd_add", record)


def test_long_proxy_flag_is_accepted(monkeypatch, tmp_path):
    seen = {}
    _wire(monkeypatch, tmp_path, True, [])
    _record_add(monkeypatch, seen)
    entry.main(["--proxy", "https://e.com/a.iso"])
    assert seen["proxy"] is True


def test_without_the_flag_the_proxy_stays_off(monkeypatch, tmp_path):
    seen = {}
    _wire(monkeypatch, tmp_path, True, [])
    _record_add(monkeypatch, seen)
    entry.main(["https://e.com/a.iso"])
    assert seen["proxy"] is False


def test_the_proxy_flag_is_not_treated_as_a_url(monkeypatch, tmp_path):
    seen = {}
    _wire(monkeypatch, tmp_path, True, [])
    _record_add(monkeypatch, seen)
    entry.main(["-p", "https://e.com/a.iso"])
    assert seen["urls"] == ["https://e.com/a.iso"]


def test_a_youtube_url_takes_the_yt_dlp_route(monkeypatch, tmp_path):
    calls = []
    seen = {}
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(entry.shutil, "which", lambda _n: "/opt/homebrew/bin/yt-dlp")
    monkeypatch.setattr(
        "dl.tui.ytflow.run_youtube",
        lambda cfg, urls, proxy=False: (seen.update(urls=urls, proxy=proxy) or ([], False)),
    )
    assert entry.main(["https://youtu.be/abc123"]) == 0
    assert seen["urls"] == ["https://youtu.be/abc123"]
    assert calls == [], "must not go through the aria2 preview"


def test_a_normal_url_never_takes_the_yt_dlp_route(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(
        "dl.tui.ytflow.run_youtube",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert calls


def test_missing_yt_dlp_is_reported_plainly(monkeypatch, tmp_path, capsys):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(entry.shutil, "which", lambda _n: None)
    assert entry.main(["https://youtu.be/abc123"]) == 1
    err = capsys.readouterr().err
    assert "yt-dlp not found" in err
    assert "brew install yt-dlp" in err


def test_youtube_without_a_terminal_explains_itself(monkeypatch, tmp_path, capsys):
    calls = []
    _wire(monkeypatch, tmp_path, False, calls)
    monkeypatch.setattr(entry.shutil, "which", lambda _n: "/opt/homebrew/bin/yt-dlp")
    assert entry.main(["https://youtu.be/abc123"]) == 1
    assert "need a terminal" in capsys.readouterr().err


def test_a_cancelled_picker_returns_130(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)

    def cancelled_preview(cfg, client, gids=(), pending=(), queue=None, pick_paths=True):
        calls.append({"gids": gids, "pending": pending, "queue": queue, "pick_paths": pick_paths})
        return ["  cancelled — nothing queued"], True

    monkeypatch.setattr(entry, "run_preview", cancelled_preview)
    assert entry.main(["https://e.com/a.iso"]) == 130


def test_a_cancelled_picker_never_queues(monkeypatch, tmp_path):
    from dl import cli

    added = []
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (added.append(a) or (0, ["gidX"])))
    monkeypatch.setattr(
        entry, "run_preview", lambda *a, **k: (["  cancelled — nothing queued"], True)
    )
    entry.main(["https://e.com/a.iso"])
    assert added == []


def test_explicit_dir_skips_the_picker(monkeypatch, tmp_path):
    """-d names the folder, so nothing is asked about it — but the download
    still goes through the duplicate check."""
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    entry.main(["-d", str(tmp_path / "x"), "https://e.com/a.iso"])
    assert calls
    assert calls[0]["pick_paths"] is False
    assert calls[0]["pending"], "still needs a request to run the duplicate check on"
    assert calls[0]["pending"][0].default_dir == tmp_path / "x"


def test_a_plain_url_still_opens_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    entry.main(["https://e.com/a.iso"])
    assert calls[0]["pick_paths"] is True


def test_no_preview_skips_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    entry.main(["--no-preview", "https://e.com/a.iso"])
    assert calls == []


def test_non_tty_skips_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, False, calls)
    entry.main(["https://e.com/a.iso"])
    assert calls == []


def test_a_bad_url_is_rejected_before_the_picker_opens(monkeypatch, tmp_path, capsys):
    """Do not make someone choose a folder for a typo."""
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.undo()
    _wire(monkeypatch, tmp_path, True, calls)
    from dl import cli

    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (1, []))
    assert entry.main(["not-a-url"]) == 1
    assert calls == []


def test_history_never_starts_the_daemon(tmp_path, monkeypatch, capsys):
    """Printing the past has nothing to ask aria2, and waiting on a daemon
    start would be a slow surprise for a read-only command."""
    from dl import config as config_module

    started = []
    monkeypatch.setattr(entry.daemon, "ensure_running", lambda *a, **k: started.append(1))
    monkeypatch.setattr(entry.config, "STATE_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config_module, "STATE_DIR", tmp_path, raising=False)

    assert entry.main(["history"]) == 0
    assert started == []


def test_history_is_a_known_subcommand():
    assert "history" in entry.SUBCOMMANDS


def test_usage_mentions_history():
    assert "dl history" in entry.USAGE
