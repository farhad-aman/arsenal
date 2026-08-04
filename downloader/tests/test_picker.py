import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from dl import theme
from dl.tui.picker import PickerScreen


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "unset"

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self):
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def make(cfg, tmp_path, records=(), filename="movie.mkv"):
    return PickerScreen(
        filename=filename,
        default_dir=tmp_path / "default",
        category=cfg.categories["video"],
        cfg=cfg,
        records=list(records),
        index=0,
        total=1,
        theme=theme.THEMES["aurora"],
    )


async def test_enter_accepts_the_preselected_default(cfg, tmp_path):
    app = Host(make(cfg, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == tmp_path / "default"


async def test_escape_dismisses_with_none(cfg, tmp_path):
    app = Host(make(cfg, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


async def test_down_then_enter_picks_the_second_candidate(cfg, tmp_path):
    records = [{"path": str(tmp_path / "series" / "a.mkv")}]
    screen = make(cfg, tmp_path, records)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        second = screen.choices[1].path
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == second


async def test_cursor_does_not_run_past_the_ends(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(50):
            await pilot.press("down")
        assert screen.cursor == len(screen.choices) - 1
        for _ in range(50):
            await pilot.press("up")
        assert screen.cursor == 0


async def test_typing_filters_the_list(cfg, tmp_path):
    records = [{"path": str(tmp_path / "series" / "a.mkv")}]
    screen = make(cfg, tmp_path, records)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(screen.choices)
        for ch in "series":
            await pilot.press(ch)
        await pilot.pause()
        assert len(screen.choices) < before
        assert any("series" in str(c.path) for c in screen.choices)


async def test_typing_a_path_offers_a_create_row(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ch in "~/brand-new":
            await pilot.press(ch)
        await pilot.pause()
        assert screen.choices[-1].kind == "create"


async def test_accepting_a_create_row_returns_the_typed_path(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    target = tmp_path / "brand-new"
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#picker-input", Input).value = str(target)
        await pilot.pause()
        screen.cursor = len(screen.choices) - 1
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == target


async def test_an_unwritable_choice_shows_an_error_and_stays_open(cfg, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    screen = make(cfg, tmp_path)
    app = Host(screen)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.query_one("#picker-input", Input).value = str(locked / "sub")
            await pilot.pause()
            screen.cursor = len(screen.choices) - 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.result == "unset"
            assert "cannot write" in screen.error
    finally:
        locked.chmod(0o700)


async def test_tab_completes_the_highlighted_path_into_the_input(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert str(tmp_path / "default") in screen.input_value


async def test_header_shows_filename_and_position(cfg, tmp_path):
    screen = PickerScreen(
        filename="ubuntu.iso",
        default_dir=tmp_path / "iso",
        category=cfg.categories["iso"],
        cfg=cfg,
        records=[],
        index=1,
        total=3,
        theme=theme.THEMES["aurora"],
    )
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "ubuntu.iso" in screen.header_text
        assert "2 of 3" in screen.header_text


async def test_the_list_scrolls_so_the_cursor_stays_visible(cfg, tmp_path):
    """With 8 built-in categories the window is full, so later candidates —
    including the current directory — are only reachable if it scrolls."""
    from dl.tui.picker import MAX_ROWS

    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(screen.choices) > MAX_ROWS, "need more candidates than fit"
        assert screen._window_start() == 0
        for _ in range(len(screen.choices)):
            await pilot.press("down")
        assert screen.cursor == len(screen.choices) - 1
        start = screen._window_start()
        assert start + MAX_ROWS > screen.cursor, "cursor scrolled out of view"
        rendered = screen.list_text
        assert "current dir" in rendered


async def test_typing_a_parent_path_lists_real_subdirectories(cfg, tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / "pictures").mkdir()
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#picker-input", Input).value = f"{tmp_path}/pro"
        await pilot.pause()
        assert tmp_path / "projects" in [c.path for c in screen.choices]
        assert tmp_path / "pictures" not in [c.path for c in screen.choices]
        assert "on disk" in screen.list_text


async def test_an_existing_directory_is_offered_once(cfg, tmp_path):
    (tmp_path / "projects").mkdir()
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#picker-input", Input).value = str(tmp_path / "projects")
        await pilot.pause()
        paths = [c.path for c in screen.choices]
        assert paths.count(tmp_path / "projects") == 1
        assert [c.kind for c in screen.choices].count("create") == 0


async def test_an_unknown_user_expansion_does_not_crash(cfg, tmp_path):
    """~s is not a home directory, and expanduser raises rather than passing it
    through."""
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ch in "~s":
            await pilot.press(ch)
        await pilot.pause()
        assert screen.input_value == "~s"


async def test_ctrl_c_cancels_the_whole_batch(cfg, tmp_path):
    from dl.tui.picker import CANCEL

    app = Host(make(cfg, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+c")
        await pilot.pause()
    assert app.result is CANCEL


async def test_the_last_candidate_is_the_current_directory(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert screen.choices[-1].kind == "cwd"
