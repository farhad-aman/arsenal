"""The mono theme has to reach every surface, not just the download table.

The point of mono is column alignment: some terminal fonts draw emoji one cell
wide instead of two, and everything after them shifts. So the check is not
"no emoji" but "nothing double-width" — which is what actually breaks.
"""

import unicodedata
from collections import deque

import pytest

from dl import cli, config, settings, theme, watch
from dl.tui.completed import render_entry
from dl.tui.settings import CategoriesScreen, FormScreen, HeadersScreen, ProxyScreen
from dl.tui.table import render_row, row_from_status
from dl.tui.ytflow import summarise as yt_summarise


def wide(text: str) -> list[str]:
    """Characters that occupy two cells, or force emoji presentation."""
    return [
        ch
        for ch in text
        if unicodedata.east_asian_width(ch) in ("W", "F") or ch == "️"
    ]


@pytest.fixture
def mono_cfg(sandbox_cfg):
    return config.replace(
        sandbox_cfg, general=config.replace(sandbox_cfg.general, theme="mono")
    )


class Recorder:
    def __init__(self):
        self.added = []
        self.active = []

    def add_uri(self, uris, options):
        self.added.append((uris, options))
        return "g1"

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return []

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_option(self, gid):
        return {"all-proxy": "http://127.0.0.1:2080"}


def status(name="ubuntu.iso"):
    return {
        "gid": "g1",
        "status": "active",
        "totalLength": "1000",
        "completedLength": "500",
        "downloadSpeed": "100",
        "connections": "8",
        "files": [{"path": f"/tmp/{name}", "uris": [{"uri": f"https://e.com/{name}"}]}],
        "errorMessage": "",
    }


def test_the_glyph_table_covers_every_emoji_it_is_asked_for():
    for symbol, plain in theme.GLYPHS.items():
        assert theme.glyph(symbol, icons=False) == plain
        assert theme.glyph(symbol, icons=True) == symbol
        assert not wide(plain), f"{symbol} maps to something still double-width"


def test_a_download_row_has_no_wide_glyphs(mono_cfg):
    row = row_from_status(status(), mono_cfg, proxied=True)
    th = theme.select(mono_cfg)
    for line in render_row(row, th, 100, selected=True, frame=0, expanded=True):
        assert not wide(line), line


def test_the_queue_line_has_no_wide_glyphs(mono_cfg, capsys):
    client = Recorder()
    cli.cmd_add(["https://e.com/a.iso"], mono_cfg, client, None)
    out = capsys.readouterr().out
    assert not wide(out), out


def test_a_skipped_duplicate_line_has_no_wide_glyphs(mono_cfg, capsys):
    from dl import duplicates

    client = Recorder()
    cli.cmd_add(
        ["https://e.com/a.iso"], mono_cfg, client, None, decisions=[duplicates.SKIP]
    )
    out = capsys.readouterr().out
    assert not wide(out), out


def test_ls_has_no_wide_glyphs(mono_cfg, capsys):
    client = Recorder()
    client.active = [status()]
    cli.cmd_ls(mono_cfg, client, use_color=False)
    out = capsys.readouterr().out
    assert not wide(out), out


def test_history_has_no_wide_glyphs(mono_cfg, tmp_path, capsys):
    from dl import history

    log = tmp_path / "history.jsonl"
    history.append(
        {
            "ts": 1785942378,
            "name": "a.iso",
            "bytes": 10,
            "path": "/tmp/a.iso",
            "category": "iso",
            "url": "https://e.com/a.iso",
            "status": "ok",
            "proxy": True,
        },
        log,
    )
    cli.cmd_history(mono_cfg, log, [])
    out = capsys.readouterr().out
    assert not wide(out), out


def test_the_note_under_the_list_has_no_wide_glyphs(mono_cfg):
    from dl import sort
    from dl.tui.searchbar import summary

    th = theme.select(mono_cfg)
    for field in sort.FIELDS + sort.DONE_FIELDS:
        for reverse in (False, True):
            badge = sort.label(sort.Order(field, reverse), th.icons)
            line = summary("ubuntu", 2, 47, th, badge)
            assert not wide(line), line


def test_the_completed_tab_has_no_wide_glyphs(mono_cfg):
    th = theme.select(mono_cfg)
    for state in ("ok", "error"):
        record = {
            "name": "a.iso",
            "status": state,
            "bytes": 10,
            "ts": 1,
            "path": "",
            "proxy": True,
        }
        line = render_entry(record, th, selected=False, now=2)
        assert not wide(line), line


def test_the_clipboard_watcher_has_no_wide_glyphs(mono_cfg, capsys):
    client = Recorder()
    watch.poll_once("https://e.com/a.iso", deque(maxlen=20), mono_cfg, client)
    out = capsys.readouterr().out
    assert not wide(out), out


def test_the_youtube_summary_has_no_wide_glyphs(mono_cfg):
    jobs = [
        {"status": "complete", "file": "/tmp/clip.mp4", "done": 10, "url": "u"},
        {"status": "error", "file": "", "error": "boom", "url": "u"},
        {"status": "active", "file": "", "url": "u"},
    ]
    for line in yt_summarise(jobs, icons=False):
        assert not wide(line), line


async def test_the_settings_screens_have_no_wide_glyphs(mono_cfg):
    from textual.app import App, ComposeResult
    from textual.widgets import Static

    class Host(App):
        CSS = """
        FormScreen, ProxyScreen, HeadersScreen, CategoriesScreen { align: center middle; }
        #settings-box { width: 76; padding: 1 2; }
        #settings-list, #settings-error { height: auto; }
        """

        def __init__(self, screen):
            super().__init__()
            self._screen = screen

        def compose(self) -> ComposeResult:
            yield Static("host")

        def on_mount(self):
            self.push_screen(self._screen)

        def reload_config(self, cfg):
            pass

    screens = [
        FormScreen("General", settings.GENERAL, mono_cfg),
        ProxyScreen(config.replace(mono_cfg, proxy_domains=("youtube.com",))),
        HeadersScreen(config.replace(mono_cfg, headers={"e.com": {"X": "1"}})),
        CategoriesScreen(mono_cfg),
    ]
    for screen in screens:
        app = Host(screen)
        async with app.run_test() as pilot:
            await pilot.pause()
            head = screen.query_one("#settings-head", Static)
            text = f"{head.renderable if hasattr(head, 'renderable') else ''}{screen.body}"
            assert not wide(text), f"{type(screen).__name__}: {text}"
