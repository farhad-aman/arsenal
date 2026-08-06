from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from ..format import cells
from .table import escape

NEWEST = 25


class PlaylistScreen(ModalScreen[int | None]):
    """How much of a collection to take.

    Dismisses with how many to queue, or None for cancel. A channel can hold
    thousands, and queuing them because a URL was pasted is not a decision
    anyone made.

    The count is all there is to show: listing a playlist flat is one request,
    but knowing how big each video is means extracting every one of them.
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        ("n", "newest", "newest"),
    ]

    def __init__(self, title: str, count: int, newest: int = NEWEST):
        super().__init__()
        self.collection = title
        self.count = count
        self.newest = newest

    @property
    def offers_newest(self) -> bool:
        return self.count > self.newest

    def compose(self) -> ComposeResult:
        with Vertical(id="playlist-box"):
            yield Label("Download a whole collection?", id="playlist-head")
            yield Static(self.summary(), id="playlist-detail")
            yield Button(f"Download all {self.count}  (⏎)", id="all")
            if self.offers_newest:
                yield Button(f"Newest {self.newest} only  (n)", id="newest")
            yield Button("Cancel  (esc)", id="cancel")

    def summary(self) -> str:
        name = escape(self.collection) if self.collection else "(untitled)"
        if cells(name) > 60:
            name = name[:57] + "…"
        return f"  {name}\n  {self.count} video{'s' if self.count != 1 else ''}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "all":
            self.dismiss(self.count)
        elif event.button.id == "newest":
            self.dismiss(self.newest)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_newest(self) -> None:
        self.dismiss(self.newest if self.offers_newest else self.count)
