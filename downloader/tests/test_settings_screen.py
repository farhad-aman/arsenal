import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from dl import settings
from dl.tui.settings import FormScreen


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class Host(App):
    CSS = """
    FormScreen, SettingsMenuScreen, ProxyScreen, HeadersScreen, CategoriesScreen {
        align: center middle;
    }
    #settings-box { width: 76; padding: 1 2; }
    #settings-list, #settings-error { height: auto; }
    """

    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "unset"
        self.reloaded = None

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self):
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))

    def reload_config(self, cfg):
        self.reloaded = cfg


def form(cfg, fields=settings.LIMITS):
    return FormScreen("Limits", fields, cfg)


async def test_it_shows_every_field_with_its_current_value(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Connections per server" in screen.body
        assert str(cfg.limits.connections) in screen.body


async def test_escape_returns_no_changes(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}


async def test_arrows_cycle_a_choice_field(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = screen.values[("general", "theme")]
        await pilot.press("right")
        await pilot.pause()
        assert screen.values[("general", "theme")] != before


async def test_arrows_toggle_a_bool_field(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        before = screen.values[("general", "ascii_icons")]
        await pilot.press("right")
        await pilot.pause()
        assert screen.values[("general", "ascii_icons")] is not before


async def test_a_live_field_previews_at_once(cfg):
    """Theme has to be seen to be chosen."""
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        assert app.reloaded is not None
        assert app.reloaded.general.theme == screen.values[("general", "theme")]


async def test_escape_puts_a_previewed_theme_back(cfg):
    """A preview that survives a cancel is not a preview."""
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert app.reloaded.general.theme == cfg.general.theme


async def test_a_field_that_is_not_live_does_not_preview(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "9"
        await pilot.press("enter")
        await pilot.pause()
        assert app.reloaded is None


async def test_enter_opens_an_editor_on_a_text_field(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert screen.editing is True


async def test_a_typed_value_is_kept(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "9"
        await pilot.press("enter")
        await pilot.pause()
        assert screen.values[("general", "max_concurrent")] == 9
        assert screen.editing is False


async def test_a_bad_value_is_refused_with_a_reason(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "banana"
        await pilot.press("enter")
        await pilot.pause()
        assert "number" in screen.error.lower()
        assert screen.editing is True


async def test_escape_while_editing_cancels_only_the_edit(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "9"
        await pilot.press("escape")
        await pilot.pause()
        assert screen.editing is False
        assert app.result == "unset", "the screen itself must stay open"


async def test_saving_returns_only_what_changed(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert list(app.result) == [("general", "theme")]


async def test_saving_with_nothing_changed_returns_nothing(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result == {}


async def test_the_input_is_hidden_until_it_is_needed(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert screen.query_one("#settings-input", Input).display is False
        await pilot.press("enter")
        await pilot.pause()
        assert screen.query_one("#settings-input", Input).display is True
