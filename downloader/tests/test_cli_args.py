"""Argument handling.

Hand-rolled parsing dropped anything starting with '-' and only honoured -d and
-f in first position, so `dl <url> -d /dir` queued the directory as a URL and a
mistyped flag did nothing at all — both silently.
"""

import pytest

from dl import __main__ as entry


@pytest.fixture
def wired(monkeypatch, tmp_path):
    """Capture what reaches cmd_add without touching a daemon or a terminal."""
    seen = {}

    def fake_add(urls, cfg, client, explicit_dir=None, chosen=None, proxy=False,
                 decisions=None, headers=None):
        seen["urls"] = list(urls)
        seen["dir"] = explicit_dir
        seen["proxy"] = proxy
        seen["headers"] = list(headers or [])
        return 0, []

    monkeypatch.setattr(entry.cli, "cmd_add", fake_add)
    monkeypatch.setattr(entry.daemon, "ensure_running", lambda *a, **k: object())
    monkeypatch.setattr(entry.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(entry.config, "CONFIG_FILE", tmp_path / "config.toml", raising=False)
    return seen


def test_dir_flag_is_honoured_after_the_url(wired):
    assert entry.main(["https://e.com/a.iso", "-d", "/tmp/dest"]) == 0
    assert wired["urls"] == ["https://e.com/a.iso"]
    assert str(wired["dir"]) == "/tmp/dest"


def test_dir_flag_is_honoured_before_the_url(wired):
    assert entry.main(["-d", "/tmp/dest", "https://e.com/a.iso"]) == 0
    assert wired["urls"] == ["https://e.com/a.iso"]
    assert str(wired["dir"]) == "/tmp/dest"


def test_the_directory_never_arrives_as_a_url(wired):
    entry.main(["https://e.com/a.iso", "-d", "/tmp/dest"])
    assert "/tmp/dest" not in wired["urls"]


def test_long_form_dir_works_too(wired):
    entry.main(["https://e.com/a.iso", "--dir", "/tmp/dest"])
    assert str(wired["dir"]) == "/tmp/dest"


def test_a_home_relative_directory_is_expanded(wired):
    entry.main(["https://e.com/a.iso", "-d", "~/Movies"])
    assert "~" not in str(wired["dir"])


def test_an_unknown_flag_is_refused(capsys):
    assert entry.main(["https://e.com/a.iso", "--bogus-flag"]) == 1
    assert "bogus-flag" in capsys.readouterr().err


def test_a_mistyped_no_preview_is_refused(capsys):
    assert entry.main(["https://e.com/a.iso", "--no-preveiw"]) == 1
    assert "no-preveiw" in capsys.readouterr().err


def test_a_missing_url_file_is_reported_not_raised(capsys):
    assert entry.main(["-f", "/nonexistent-url-file"]) == 1
    said = capsys.readouterr().err
    assert "dl:" in said and "nonexistent-url-file" in said


def test_dash_h_without_a_value_is_still_an_error(capsys):
    assert entry.main(["-H"]) == 1
    assert "-H" in capsys.readouterr().err


def test_headers_are_collected_from_anywhere(wired):
    entry.main(["-H", "Referer: https://x/", "https://e.com/a.iso", "-H", "X-A: b"])
    assert wired["urls"] == ["https://e.com/a.iso"]
    assert wired["headers"] == ["Referer: https://x/", "X-A: b"]


def test_proxy_flag_is_position_independent(wired):
    entry.main(["https://e.com/a.iso", "-p"])
    assert wired["proxy"] is True


def test_urls_from_a_file_and_the_command_line_are_both_taken(wired, tmp_path):
    listing = tmp_path / "urls.txt"
    listing.write_text("https://e.com/one.iso\n# a comment\n\nhttps://e.com/two.iso\n")
    entry.main(["-f", str(listing), "https://e.com/three.iso"])
    assert wired["urls"] == [
        "https://e.com/one.iso",
        "https://e.com/two.iso",
        "https://e.com/three.iso",
    ]


def test_no_preview_still_skips_the_preview(wired, monkeypatch):
    attached = []
    monkeypatch.setattr(entry, "run_preview", lambda *a, **k: attached.append(1) or ([], False))
    monkeypatch.setattr(entry.sys.stdout, "isatty", lambda: True)
    entry.main(["https://e.com/a.iso", "--no-preview"])
    assert attached == []


def test_a_bare_url_still_works(wired):
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert wired["urls"] == ["https://e.com/a.iso"]


def test_a_non_url_still_reaches_cmd_add(wired):
    """'oops' is not a flag, so it is a download that will fail on its own."""
    assert entry.main(["oops"]) == 0
    assert wired["urls"] == ["oops"]


def test_help_still_returns_zero(capsys):
    assert entry.main(["--help"]) == 0
    assert "dl history" in capsys.readouterr().out


def test_an_unknown_subcommand_flag_is_refused(monkeypatch, capsys):
    monkeypatch.setattr(entry.daemon, "ensure_running", lambda *a, **k: object())
    assert entry.main(["ls", "--bogus"]) == 1
    assert "bogus" in capsys.readouterr().err


def test_kill_still_takes_strays(monkeypatch):
    seen = []
    monkeypatch.setattr(entry.cli, "cmd_strays", lambda state: seen.append(state) or 0)
    assert entry.main(["kill", "--strays"]) == 0
    assert seen


def test_ls_still_takes_a_name(monkeypatch):
    seen = {}
    monkeypatch.setattr(entry.daemon, "ensure_running", lambda *a, **k: object())
    monkeypatch.setattr(
        entry.cli, "cmd_ls",
        lambda cfg, client, use_color, query="", as_json=False: seen.update(query=query) or 0,
    )
    assert entry.main(["ls", "ubuntu"]) == 0
    assert seen["query"] == "ubuntu"
