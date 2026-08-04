import subprocess

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

SCHEMES = ("http://", "https://", "ftp://", "magnet:")


def clipboard_text() -> str:
    try:
        value = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False, timeout=2
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return value if value.startswith(SCHEMES) else ""


class AddUrlModal(ModalScreen[list[str] | None]):
    BINDINGS = [("escape", "dismiss_none", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Label("Add downloads — one URL per line")
            yield TextArea(clipboard_text(), id="urls")
            yield Button("Queue", variant="primary", id="ok")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        raw = self.query_one("#urls", TextArea).text
        urls = [line.strip() for line in raw.splitlines() if line.strip()]
        self.dismiss(urls or None)

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class SpeedLimitModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss_none", "cancel")]

    def __init__(self, current: str):
        super().__init__()
        self.current = "off" if current in ("0", "", "off") else current

    def compose(self) -> ComposeResult:
        with Vertical(id="limit-box"):
            yield Label("Speed limit — e.g. 2M, 500K, or off")
            yield Input(self.current, id="rate")
            yield Button("Apply", variant="primary", id="ok")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(self.query_one("#rate", Input).value.strip())

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self.query_one("#rate", Input).value.strip())

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss_false", "cancel")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.question)
            yield Button("Yes", variant="error", id="yes")
            yield Button("No", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_dismiss_false(self) -> None:
        self.dismiss(False)
