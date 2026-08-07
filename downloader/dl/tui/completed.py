import time
from pathlib import Path

from textual.widgets import Static

from .. import clock
from ..format import human_bytes
from ..theme import Theme, glyph
from .table import escape

MAX_ROWS = 200


def record_path(record: dict) -> Path | None:
    raw = record.get("path") or ""
    return Path(raw) if raw else None


def render_entry(record: dict, theme: Theme, selected: bool, now: int) -> str:
    ok = record.get("status") == "ok"
    mark = glyph("✅" if ok else "❌", theme.icons)
    marker = "▌" if selected else " "
    name = escape(record.get("name", "")) or "(unnamed)"
    size = human_bytes(int(record.get("bytes", 0) or 0))
    category = record.get("category", "")
    when = clock.stamp(record.get("ts"), now).ljust(clock.CELL)
    missing = "" if _exists(record) else "  (file gone)"
    via = f'  {glyph("🌐", theme.icons)}' if record.get("proxy") else ""
    line = f"{marker} {mark}  {name:<44} {size:>10}  {category:<9} {when}{missing}{via}".rstrip()
    if theme.mono:
        return line
    color = theme.ok if ok else theme.danger
    return f"[{color}]{line}[/]" if selected else f"[{theme.dim}]{line}[/]"


def _exists(record: dict) -> bool:
    path = record_path(record)
    return bool(path and path.exists())


class CompletedTable(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.rows: list[dict] = []
        self.cursor = 0
        self.search_query = ""

    @property
    def selected(self) -> dict | None:
        if not self.rows:
            return None
        return self.rows[min(self.cursor, len(self.rows) - 1)]

    def load(self, log: Path, query: str = "", order=None) -> None:
        from .. import history, sort

        self.search_query = query
        anchor = self.selected
        newest_first = history.find(log, query, MAX_ROWS)[::-1]
        self.rows = sort.apply_records(newest_first, order or sort.DONE_DEFAULT)
        self.cursor = self._locate(anchor)
        self.refresh_view()

    def _locate(self, record: dict | None) -> int:
        """Where the selected download sits now the list has been rebuilt.

        Rows arrive newest-first, so every download that finishes pushes the
        rest down one. Holding the index would slide the selection onto a
        different file — on the tab that offers delete and re-download.
        """
        if record is not None:
            from .. import history

            wanted = history.key(record)
            for index, row in enumerate(self.rows):
                if history.key(row) == wanted:
                    return index
        return min(self.cursor, max(len(self.rows) - 1, 0))

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        self.refresh_view()

    def cursor_span(self) -> tuple[int, int]:
        return (self.cursor, 1) if self.rows else (0, 0)

    def refresh_view(self) -> None:
        if not self.rows:
            from .searchbar import empty_note

            self.update(
                empty_note(self.search_query, self.theme_data)
                if self.search_query
                else "  (nothing finished yet)"
            )
            return
        now = int(time.time())
        self.update(
            "\n".join(
                render_entry(record, self.theme_data, index == self.cursor, now)
                for index, record in enumerate(self.rows)
            )
        )
