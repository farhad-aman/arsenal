import time
from pathlib import Path

from textual.widgets import Static

from ..format import human_bytes, human_duration
from ..theme import Theme
from .table import escape

MAX_ROWS = 200


def record_path(record: dict) -> Path | None:
    raw = record.get("path") or ""
    return Path(raw) if raw else None


def render_entry(record: dict, theme: Theme, selected: bool, now: int) -> str:
    ok = record.get("status") == "ok"
    mark = "✅" if ok else "❌"
    marker = "▌" if selected else " "
    name = escape(record.get("name", "")) or "(unnamed)"
    size = human_bytes(int(record.get("bytes", 0) or 0))
    category = record.get("category", "")
    age = human_duration(max(now - int(record.get("ts", 0) or 0), 0))
    missing = "" if _exists(record) else "  (file gone)"
    line = f"{marker} {mark}  {name:<44} {size:>10}  {category:<9} {age} ago{missing}"
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

    @property
    def selected(self) -> dict | None:
        if not self.rows:
            return None
        return self.rows[min(self.cursor, len(self.rows) - 1)]

    def load(self, log: Path) -> None:
        from .. import history

        self.rows = history.tail(log, MAX_ROWS)[::-1]
        self.cursor = min(self.cursor, max(len(self.rows) - 1, 0))
        self.refresh_view()

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        self.refresh_view()

    def refresh_view(self) -> None:
        if not self.rows:
            self.update("  (nothing finished yet)")
            return
        now = int(time.time())
        self.update(
            "\n".join(
                render_entry(record, self.theme_data, index == self.cursor, now)
                for index, record in enumerate(self.rows)
            )
        )
