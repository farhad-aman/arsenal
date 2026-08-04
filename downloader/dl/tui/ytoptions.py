from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ..youtube import (
    AUDIO_CHOICES,
    AUDIO_CONTAINERS,
    DEFAULTS,
    SUB_CHOICES,
    VIDEO_CHOICES,
    VIDEO_CONTAINERS,
    Choices,
)

LANGS = ("en", "fa", "ar", "tr", "fr", "es", "de", "ru", "hi", "ja")
HINT = "↑↓ field    ←→ change    ⏎ continue    esc cancel"

LABELS = {
    "video": "Video quality",
    "audio": "Audio quality",
    "subs": "Subtitles",
    "sub_lang": "Subtitle language",
    "container": "Format",
}
SHOWN = {
    "best": "best available",
    "none": "audio only",
    "off": "off",
    "soft": "soft — selectable track",
    "hard": "hard — burned into the picture",
}


class YouTubeOptionsScreen(ModalScreen[Choices | None]):
    """Quality, subtitles and container for one YouTube download.

    Appears before the destination picker: what gets downloaded is decided
    first, where it lands second.
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous_field", "up", priority=True),
        Binding("down", "next_field", "down", priority=True),
        Binding("left", "previous_value", "left", priority=True),
        Binding("right", "next_value", "right", priority=True),
        Binding("enter", "accept", "continue", priority=True),
    ]

    FIELDS = ("video", "audio", "subs", "sub_lang", "container")

    def __init__(self, title: str, choices: Choices = DEFAULTS, can_burn: bool = True):
        super().__init__()
        self.video_title = title
        self.can_burn = can_burn
        self.values = dict(
            video=choices.video,
            audio=choices.audio,
            subs=choices.subs,
            sub_lang=choices.sub_lang,
            container=choices.container,
        )
        self.field = 0
        self.body = ""
        self.head = f"  ▶  {title}"

    @property
    def audio_only(self) -> bool:
        return self.values["video"] == "none"

    def options_for(self, field: str) -> tuple[str, ...]:
        if field == "video":
            return VIDEO_CHOICES
        if field == "audio":
            return AUDIO_CHOICES
        if field == "subs":
            return SUB_CHOICES
        if field == "sub_lang":
            return LANGS
        return AUDIO_CONTAINERS if self.audio_only else VIDEO_CONTAINERS

    def visible_fields(self) -> tuple[str, ...]:
        """Subtitles are meaningless without a picture, and the language only
        matters once subtitles are on."""
        if self.audio_only:
            return ("video", "audio", "container")
        if self.values["subs"] == "off":
            return ("video", "audio", "subs", "container")
        return self.FIELDS

    def compose(self) -> ComposeResult:
        with Vertical(id="yt-box"):
            yield Static(self.head, id="yt-head")
            yield Static("", id="yt-list")
            yield Static(HINT, id="yt-hint")

    def on_mount(self) -> None:
        self._repaint()

    def _repaint(self) -> None:
        rows = []
        for index, field in enumerate(self.visible_fields()):
            value = self.values[field]
            shown = SHOWN.get(value, value)
            if field == "video" and value not in ("best", "none"):
                shown = f"{value}p"
            if field == "audio" and value != "best":
                shown = f"{value} kbps"
            marker = "▌" if index == self.field else " "
            rows.append(f"{marker} {LABELS[field]:<20} ‹ {shown} ›")
        if self.values["subs"] == "hard" and not self.can_burn:
            rows.append("")
            rows.append("  ⚠  this ffmpeg cannot burn in subtitles (built without")
            rows.append("     libass) — soft subtitles will still work")
        self.body = "\n".join(rows)
        self.query_one("#yt-list", Static).update(self.body)

    def _current_field(self) -> str:
        fields = self.visible_fields()
        return fields[min(self.field, len(fields) - 1)]

    def _step(self, delta: int) -> None:
        field = self._current_field()
        options = self.options_for(field)
        position = options.index(self.values[field]) if self.values[field] in options else 0
        self.values[field] = options[(position + delta) % len(options)]
        if field == "video":
            self._settle_container()
        self.field = min(self.field, len(self.visible_fields()) - 1)
        self._repaint()

    def _settle_container(self) -> None:
        """Switching to audio-only leaves an impossible container behind."""
        allowed = self.options_for("container")
        if self.values["container"] not in allowed:
            self.values["container"] = allowed[0]

    def action_next_field(self) -> None:
        self.field = (self.field + 1) % len(self.visible_fields())
        self._repaint()

    def action_previous_field(self) -> None:
        self.field = (self.field - 1) % len(self.visible_fields())
        self._repaint()

    def action_next_value(self) -> None:
        self._step(1)

    def action_previous_value(self) -> None:
        self._step(-1)

    def action_accept(self) -> None:
        self.dismiss(Choices(**self.values))

    def action_cancel(self) -> None:
        self.dismiss(None)
