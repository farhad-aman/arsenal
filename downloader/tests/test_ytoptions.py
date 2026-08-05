import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from dl.tui.ytoptions import YouTubeOptionsScreen
from dl.youtube import DEFAULTS


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "unset"

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self):
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def make(**over):
    return YouTubeOptionsScreen("Some Video Title", DEFAULTS)


async def test_enter_accepts_the_defaults():
    app = Host(make())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == DEFAULTS


async def test_escape_cancels():
    app = Host(make())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


async def test_the_title_is_shown():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Some Video Title" in screen.head


async def test_right_arrow_lowers_the_video_quality():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("enter")
        await pilot.pause()
    assert app.result.video == "2160"


async def test_left_arrow_wraps_to_audio_only():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert screen.values["video"] == "none"


async def test_choosing_audio_only_switches_the_container():
    """mp4 is not an audio container."""
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert screen.values["container"] == "m4a"


async def test_audio_only_hides_the_subtitle_fields():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("left")
        await pilot.pause()
        assert "subs" not in screen.visible_fields()
        assert "Subtitles" not in screen.body


async def test_the_language_appears_only_once_subtitles_are_on():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "sub_lang" not in screen.visible_fields()
        await pilot.press("down")
        await pilot.press("down")
        await pilot.press("right")
        await pilot.pause()
        assert screen.values["subs"] == "soft"
        assert "sub_lang" in screen.visible_fields()


async def test_moving_down_then_changing_edits_the_second_field():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("right")
        await pilot.press("enter")
        await pilot.pause()
    assert app.result.audio == "256"
    assert app.result.video == "best"


async def test_the_selected_field_is_marked():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert screen.body.splitlines()[0].startswith("▌")
        await pilot.press("down")
        await pilot.pause()
        assert screen.body.splitlines()[1].startswith("▌")


async def test_field_focus_never_falls_off_the_end_when_fields_disappear():
    """Turning subtitles on then switching to audio-only shortens the list."""
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(4):
            await pilot.press("down")
        await pilot.pause()
        screen.field = len(screen.visible_fields()) - 1
        screen.values["video"] = "none"
        screen._settle_container()
        screen.field = min(screen.field, len(screen.visible_fields()) - 1)
        screen._repaint()
        await pilot.press("right")
        await pilot.pause()
        assert screen.field < len(screen.visible_fields())


async def test_hard_subtitles_warn_when_ffmpeg_cannot_burn_them():
    screen = YouTubeOptionsScreen("Title", DEFAULTS, can_burn=False)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.values["subs"] = "hard"
        screen._repaint()
        assert "cannot burn in subtitles" in screen.body


async def test_no_warning_when_ffmpeg_can_burn_them():
    screen = YouTubeOptionsScreen("Title", DEFAULTS, can_burn=True)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.values["subs"] = "hard"
        screen._repaint()
        assert "cannot burn in" not in screen.body


async def test_a_full_selection_round_trips():
    screen = make()
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.values.update(video="1080", audio="192", subs="hard", sub_lang="fa", container="mkv")
        await pilot.press("enter")
        await pilot.pause()
    assert app.result.video == "1080"
    assert app.result.subs == "hard"
    assert app.result.sub_lang == "fa"
    assert app.result.container == "mkv"


async def test_the_burn_in_warning_names_a_remedy_that_works():
    """`brew reinstall ffmpeg` does not help: homebrew-core's formula has no
    libass at all, so the rebuild produces the same binary."""
    screen = YouTubeOptionsScreen("clip", can_burn=False)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(2):
            await pilot.press("down")
        while screen.values["subs"] != "hard":
            await pilot.press("right")
        await pilot.pause()
        text = screen.body
        assert "libass" in text
        assert "homebrew-ffmpeg" in text
        assert "reinstall" not in text
