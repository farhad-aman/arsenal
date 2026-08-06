from textual.message import Message
from textual.widgets import Input, Static

from ..theme import Theme, glyph
from .table import escape

INPUT_ID = "search-input"
NOTE_ID = "search-note"


def summary(query: str, shown: int, total: int | None, theme: Theme) -> str:
    mark = glyph("🔍", theme.icons)
    count = f"{shown} of {total}" if total is not None else f"{shown} found"
    line = f'{mark} "{escape(query)}"   {count}   esc clear'
    if theme.mono:
        return line
    return f"[{theme.accent}]{line}[/]"


def empty_note(query: str, theme: Theme) -> str:
    line = f'  nothing matches "{escape(query)}"'
    return line if theme.mono else f"[{theme.dim}]{line}[/]"


class SearchCancelled(Message):
    pass


class SearchInput(Input):
    """Escape leaves the box without leaving a filter behind.

    Input handles escape itself, so the dashboard would never see the key.
    """

    BINDINGS = [("escape", "cancel", "cancel")]

    def action_cancel(self) -> None:
        self.post_message(SearchCancelled())


class SearchNote(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.text = ""

    def show(self, query: str, shown: int, total: int | None) -> None:
        self.text = summary(query, shown, total, self.theme_data)
        self.update(self.text)
