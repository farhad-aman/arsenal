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
