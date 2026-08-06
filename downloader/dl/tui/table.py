import re
from dataclasses import dataclass, field
from pathlib import Path

from textual.widgets import Static

from ..config import Category, Config
from ..format import (
    SPINNER,
    cells,
    fit,
    human_bytes,
    human_duration,
    human_speed,
    pad,
    progress_bar,
    rpad,
    sparkline,
)
from ..routing import OTHER, resolve
from ..theme import Theme, glyph, icon_for, ramp_color


_MARKUP = re.compile(r"\[[^]]*\]")


def escape(text: str) -> str:
    return text.replace("[", "\\[")


@dataclass
class Row:
    gid: str
    name: str
    status: str
    total: int
    done: int
    speed: int
    eta: int
    category: Category
    path: Path
    conns: int
    error: str
    url: str = ""
    proxied: bool = False
    history: list[int] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return (self.done * 100.0 / self.total) if self.total else 0.0


def row_from_status(item: dict, cfg: Config, proxied: bool = False) -> Row:
    files = item.get("files") or [{}]
    first = files[0]
    path = Path(first.get("path", "") or "")
    uris = first.get("uris") or []
    url = uris[0].get("uri", "") if uris else ""
    total = int(item.get("totalLength", 0) or 0)
    done = int(item.get("completedLength", 0) or 0)
    speed = int(item.get("downloadSpeed", 0) or 0)
    category = resolve(url, path.name, cfg).category if path.name or url else OTHER
    eta = (total - done) // speed if speed > 0 and total > done else -1
    return Row(
        gid=item.get("gid", ""),
        name=path.name,
        status=item.get("status", ""),
        total=total,
        done=done,
        speed=speed,
        eta=eta,
        category=category,
        path=path,
        conns=int(item.get("connections", 0) or 0),
        error=item.get("errorMessage", "") or "",
        url=url,
        proxied=proxied,
    )


YT_PREFIX = "yt-"
YT_STATUS = {"queued": "waiting", "active": "active", "burning": "active"}


def is_youtube_row(row: Row) -> bool:
    """yt-dlp jobs share the table with aria2 downloads but none of its RPC."""
    return row.gid.startswith(YT_PREFIX)


def row_from_job(job: dict, cfg: Config) -> Row:
    """A yt-dlp download rendered with the same shape as an aria2 one."""
    landed = Path(job.get("file") or "")
    name = landed.name or job.get("title") or job.get("url", "")
    if not landed.name and job.get("title"):
        name = f"{job['title']}.{job.get('choices', {}).get('container', 'mp4')}"
    done = int(job.get("done", 0) or 0)
    total = int(job.get("total", 0) or 0)
    speed = int(job.get("speed", 0) or 0)
    category = cfg.categories.get("video") or OTHER
    return Row(
        gid=job.get("id", ""),
        name=name,
        status=YT_STATUS.get(job.get("status", ""), job.get("status", "")),
        total=total,
        done=done,
        speed=speed,
        eta=(total - done) // speed if speed > 0 and total > done else -1,
        category=category,
        path=landed if landed.name else Path(job.get("dir", "")),
        conns=0,
        error=job.get("error", "") or "",
        url=job.get("url", ""),
        proxied=bool(job.get("proxy")),
    )


ICON_CELL = 2
NAME_CELL = 44
SIZE_CELL = 20
STATE_CELL = 15
SPARK_CELL = 8
ETA_CELL = 11


def columns_for_width(width: int) -> set[str]:
    columns = set()
    if width >= 80:
        columns.add("folder")
    if width >= 66:
        columns.add("eta")
    if width >= 56:
        columns.add("spark")
    return columns


def bar_width_for(width: int) -> int:
    return max(4, min(26, (width - 46) // 2 + 8))


def _paint(text: str, color: str, theme: Theme) -> str:
    return text if theme.mono else f"[{color}]{text}[/]"


def _cell(painted: str, width: int) -> str:
    """Pad a already-marked-up string to a column, measuring only what is drawn."""
    return painted + " " * max(width - cells(_MARKUP.sub("", painted)), 0)


def _gradient_bar(row: Row, theme: Theme, width: int) -> str:
    plain = progress_bar(row.pct, width)
    if theme.mono:
        return plain
    return "".join(
        f"[{ramp_color(theme, i / max(width - 1, 1))}]{ch}[/]" for i, ch in enumerate(plain)
    )


def _state_cell(row: Row, theme: Theme, frame: int) -> str:
    if row.status == "error":
        mark = glyph("❌", theme.icons)
        return _paint(
            f"{mark} {escape(row.error or 'failed')} — press r to retry", theme.danger, theme
        )
    if row.status == "paused":
        return _paint(f'{glyph("⏸", theme.icons)}  paused', theme.warn, theme)
    if row.status in ("waiting", "queued"):
        return _paint(f"{SPINNER[frame % len(SPINNER)]}  queued", theme.dim, theme)
    if row.status == "complete":
        return _paint(f'{glyph("✅", theme.icons)} done', theme.ok, theme)
    return _paint(f'{glyph("🚀", theme.icons)} {human_speed(row.speed)}', theme.accent, theme)


def render_row(
    row: Row, theme: Theme, width: int, selected: bool, frame: int, expanded: bool = False
) -> list[str]:
    columns = columns_for_width(width)
    marker = _paint("▌", row.category.hue, theme) if selected else " "
    icon = pad(icon_for(row.category, theme), ICON_CELL)
    sizes = (
        f"{human_bytes(row.done)} / {human_bytes(row.total)}" if row.total else human_bytes(row.done)
    )
    name = row.name or "(resolving…)"
    if row.proxied:
        name = f'{name} {glyph("🌐", theme.icons)}'
    # Padded on display width, not codepoints: one emoji or fullwidth
    # character in a name would otherwise pull every later column left.
    label = escape(fit(name, NAME_CELL))

    head = f"{marker} {icon} {label} {rpad(sizes, SIZE_CELL)}"

    # Every cell keeps its column so the eye can run straight down a stack of
    # rows: "done" and "8.1 MB/s" are different lengths, and left unpadded they
    # shove the sparkline and the ETA around on every line.
    parts = [
        f"{marker}     {_gradient_bar(row, theme, bar_width_for(width))}",
        f"{row.pct:>4.0f}%",
        _cell(_state_cell(row, theme, frame), STATE_CELL),
    ]
    if "spark" in columns:
        parts.append(_cell(_paint(sparkline(row.history, 8), theme.dim, theme), SPARK_CELL))
    if "eta" in columns:
        eta = _paint(f'{glyph("⏱", theme.icons)} {human_duration(row.eta)}', theme.dim, theme)
        parts.append(_cell(eta, ETA_CELL))
    if "folder" in columns:
        parts.append(_paint(row.category.name.upper(), row.category.hue, theme))
    body = "  ".join(parts)

    lines = [head, body]
    if selected and expanded:
        lines.append(
            f'{marker}     {glyph("📂", theme.icons)} {escape(str(row.path))} · {row.conns} conns'
        )
    return lines


ROW_LINES = 3


class DownloadTable(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.rows: list[Row] = []
        self.cursor = 0
        self.frame = 0
        self.expanded = False
        self.text = ""
        self.placeholder = ""

    @property
    def selected_gid(self) -> str | None:
        if not self.rows:
            return None
        return self.rows[min(self.cursor, len(self.rows) - 1)].gid

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        self.refresh_view()

    def cursor_span(self) -> tuple[int, int]:
        """Which lines of the rendered block the selected row occupies, so the
        scroller can be told where to look."""
        start = 0
        for index, row in enumerate(self.rows):
            height = ROW_LINES + (1 if index == self.cursor and self.expanded else 0)
            if index == self.cursor:
                return start, height
            start += height
        return 0, 0

    def set_rows(self, rows: list[Row]) -> None:
        previous = {r.gid: r.history for r in self.rows}
        for row in rows:
            row.history = (previous.get(row.gid, []) + [row.speed])[-8:]
        self.rows = rows
        self.cursor = min(self.cursor, max(len(rows) - 1, 0))
        self.refresh_view()

    def refresh_view(self) -> None:
        self.frame += 1
        width = self.size.width or 100
        lines: list[str] = []
        for index, row in enumerate(self.rows):
            selected = index == self.cursor
            lines.extend(
                render_row(
                    row, self.theme_data, width, selected, self.frame, selected and self.expanded
                )
            )
            lines.append("")
        self.text = "\n".join(lines) if self.rows else self.placeholder
        self.update(self.text)

    def render_lines_count(self) -> list[str]:
        return self.text.splitlines()
