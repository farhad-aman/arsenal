from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ..config import Category, Config
from ..destinations import (
    Candidate,
    candidates,
    create_candidate,
    display_path,
    ensure_writable,
    filter_candidates,
)
from ..theme import Theme
from .table import escape

MAX_ROWS = 8
PICKER_HINT = "⏎ accept    ↑↓ choose    esc use default    ^C cancel all"


class PickerScreen(ModalScreen[Path | None]):
    """Choose where one download should be saved.

    Dismisses with the chosen directory, or None meaning "use the routed
    default". up/down are priority bindings so the focused filter Input cannot
    swallow them.
    """

    BINDINGS = [
        ("escape", "use_default", "default"),
        Binding("up", "move_up", "up", priority=True),
        Binding("down", "move_down", "down", priority=True),
        Binding("tab", "complete", "complete", priority=True),
    ]

    def __init__(
        self,
        filename: str,
        default_dir: Path,
        category: Category,
        cfg: Config,
        records: list[dict],
        index: int,
        total: int,
        theme: Theme,
    ):
        super().__init__()
        self.filename = filename
        self.default_dir = default_dir
        self.category = category
        self.cfg = cfg
        self.records = records
        self.index = index
        self.total = total
        self.theme_data = theme
        self.all_candidates = candidates(
            filename, default_dir, category, cfg, records, Path.cwd()
        )
        self.choices: list[Candidate] = list(self.all_candidates)
        self.cursor = 0
        self.error = ""
        self.input_value = ""

    @property
    def header_text(self) -> str:
        return f"  Save  {self.filename}          file {self.index + 1} of {self.total}"

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(self.header_text, id="picker-head")
            yield Input(placeholder="filter, or type a path…", id="picker-input")
            yield Static("", id="picker-list")
            yield Static("", id="picker-error")
            yield Static(PICKER_HINT, id="picker-hint")

    def on_mount(self) -> None:
        self.query_one("#picker-input", Input).focus()
        self._repaint()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.input_value = event.value
        self._rebuild()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._accept()

    def _rebuild(self) -> None:
        items = filter_candidates(self.input_value, self.all_candidates)
        made = create_candidate(self.input_value)
        if made is not None:
            items = items + [made]
        self.choices = items
        self.cursor = min(self.cursor, max(len(items) - 1, 0))
        self.error = ""
        self._repaint()

    def _repaint(self) -> None:
        rows = []
        for position, item in enumerate(self.choices[:MAX_ROWS]):
            selected = position == self.cursor
            marker = "▌" if selected else " "
            icon = item.icon if self.theme_data.icons else item.kind[:2].upper().ljust(2)
            shown = escape(display_path(item.path))
            line = f"{marker} {icon}  {shown:<44} {item.note}"
            if selected and not self.theme_data.mono:
                line = f"[{self.theme_data.accent}]{line}[/]"
            rows.append(line)
        self.query_one("#picker-list", Static).update("\n".join(rows) or "  (no match)")
        self.query_one("#picker-error", Static).update(
            f"  ⚠ {self.error}" if self.error else ""
        )

    def action_move_down(self) -> None:
        if self.choices:
            self.cursor = min(self.cursor + 1, len(self.choices) - 1)
            self._repaint()

    def action_move_up(self) -> None:
        if self.choices:
            self.cursor = max(self.cursor - 1, 0)
            self._repaint()

    def action_complete(self) -> None:
        if not self.choices:
            return
        field = self.query_one("#picker-input", Input)
        field.value = str(self.choices[self.cursor].path)
        self.input_value = field.value

    def action_use_default(self) -> None:
        self.dismiss(None)

    def _accept(self) -> None:
        if not self.choices:
            return
        chosen = self.choices[min(self.cursor, len(self.choices) - 1)].path
        if not ensure_writable(chosen):
            self.error = f"cannot write to {chosen}"
            self._repaint()
            return
        self.dismiss(chosen)
