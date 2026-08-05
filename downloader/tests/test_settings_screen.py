import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Static

from dl import config, settings
from dl.tui.settings import (
    FormScreen,
    HeadersScreen,
    ProxyScreen,
    SettingsMenuScreen,
)


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


SAMPLE = """\
[general]
theme = "aurora"
max_concurrent = 3
"""


def menu(cfg, tmp_path, text=SAMPLE):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return SettingsMenuScreen(cfg, path), path


async def test_the_menu_lists_every_section(cfg, tmp_path):
    screen, _ = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for name in ("General", "Limits", "YouTube", "Hooks", "Proxy", "Headers", "Categories"):
            assert name in screen.body


async def test_a_broken_config_refuses_to_be_edited(cfg, tmp_path):
    """config.load() falls back to defaults on a broken file, so saving would
    write those defaults over whatever the user actually wrote."""
    screen, _ = menu(cfg, tmp_path, '[general]\ntheme = "aurora\n')
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "syntax error" in screen.body.lower()
        assert screen.blocked is True


async def test_a_broken_config_is_never_written_to(cfg, tmp_path):
    broken = '[general]\ntheme = "aurora\n'
    screen, path = menu(cfg, tmp_path, broken)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert path.read_text() == broken


async def test_escape_closes_the_menu(cfg, tmp_path):
    screen, _ = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


async def test_saving_a_section_writes_it_to_the_file(cfg, tmp_path):
    screen, path = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.save({("general", "theme"): "ember"})
        await pilot.pause()
    assert 'theme = "ember"' in path.read_text()


async def test_opening_a_section_pushes_its_form(cfg, tmp_path):
    screen, _ = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert type(app.screen).__name__ == "FormScreen"


def proxied(cfg, domains=("youtube.com",)):
    return config.replace(cfg, proxy_domains=tuple(domains))


async def test_the_proxy_screen_shows_url_and_domains(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert cfg.proxy in screen.body
        assert "youtube.com" in screen.body


async def test_a_domain_can_be_added(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.pause()
        assert "github.com" in screen.domains


async def test_a_blank_domain_is_refused(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("   ")
        await pilot.pause()
        assert screen.domains == ["youtube.com"]
        assert screen.error


async def test_a_duplicate_domain_is_refused(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("youtube.com")
        await pilot.pause()
        assert screen.domains == ["youtube.com"]
        assert screen.error


async def test_a_domain_can_be_deleted(cfg):
    screen = ProxyScreen(proxied(cfg, ("youtube.com", "github.com")))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.cursor = 1
        screen.delete_selected()
        await pilot.pause()
        assert screen.domains == ["youtube.com"]


async def test_the_url_itself_can_be_changed(cfg):
    """Displaying it without a way to change it would leave the one setting
    the whole screen is named after uneditable."""
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert screen.editing == "url"
        screen.query_one("#settings-input", Input).value = "http://127.0.0.1:1080"
        await pilot.press("enter")
        await pilot.pause()
        assert screen.url == "http://127.0.0.1:1080"


async def test_saving_returns_url_and_domains(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result[("proxy", "domains")] == ["youtube.com", "github.com"]
    assert app.result[("proxy", "url")] == cfg.proxy


async def test_escape_returns_nothing_from_the_proxy_screen(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}


RULES = {"indllserver.info": {"Referer": "https://indllserver.info/", "User-Agent": "Mozilla/5.0"}}


async def test_headers_are_shown_as_flat_rows(cfg):
    """Two levels of TOML nesting, one flat list — no sub-screen to drill into."""
    screen = HeadersScreen(config.replace(cfg, headers=RULES))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(screen.rules) == 2
        assert "indllserver.info" in screen.body
        assert "Referer" in screen.body


async def test_a_rule_can_be_added(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | X-Token | abc")
        await pilot.pause()
        assert ("e.com", "X-Token", "abc") in screen.rules


async def test_a_rule_needs_all_three_parts(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | X-Token")
        await pilot.pause()
        assert screen.rules == []
        assert "host | key | value" in screen.error


async def test_a_rule_can_be_deleted(cfg):
    screen = HeadersScreen(config.replace(cfg, headers=RULES))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.cursor = 0
        screen.delete_selected()
        await pilot.pause()
        assert len(screen.rules) == 1


async def test_the_a_key_opens_the_rule_editor(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert screen.editing is True


async def test_saving_rebuilds_the_nesting(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | Referer | https://e.com/")
        screen.add_rule("e.com | X-Token | abc")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result[("headers",)] == {
        "e.com": {"Referer": "https://e.com/", "X-Token": "abc"}
    }


async def test_a_header_value_is_shown_because_you_are_editing_it(cfg):
    screen = HeadersScreen(config.replace(cfg, headers={"e.com": {"Cookie": "s=SECRET"}}))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "SECRET" in screen.body


async def test_escape_returns_nothing_from_the_headers_screen(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | X | y")
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}
