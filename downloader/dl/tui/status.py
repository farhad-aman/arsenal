from dataclasses import dataclass

from textual.widgets import Static

from ..format import cells, human_duration, human_speed, pad, rpad, sparkline
from ..theme import Theme, glyph, ramp_color


@dataclass(frozen=True)
class Stats:
    speed: int
    active: int
    waiting: int
    done: int
    elapsed: int


def stats_from(global_stat: dict, elapsed: int) -> Stats:
    return Stats(
        speed=int(global_stat.get("downloadSpeed", 0) or 0),
        active=int(global_stat.get("numActive", 0) or 0),
        waiting=int(global_stat.get("numWaiting", 0) or 0),
        done=int(global_stat.get("numStopped", 0) or 0),
        elapsed=elapsed,
    )


def _graph(history: list[int], theme: Theme, width: int) -> str:
    line = sparkline(history, width)
    if theme.mono:
        return line
    peak = max(history) if history else 0
    window = ([0] * width + list(history))[-width:]
    return "".join(
        f"[{ramp_color(theme, (value / peak) if peak else 0.0)}]{glyph}[/]"
        for value, glyph in zip(window, line)
    )


def _marks(stats: Stats, theme: Theme):
    return (
        (glyph("⬇", theme.icons), stats.active, "active"),
        (glyph("⏳", theme.icons), stats.waiting, "queued"),
        (glyph("✅", theme.icons), stats.done, "done"),
    )


def _counters(stats: Stats, theme: Theme) -> str:
    """Each counter keeps a fixed cell so a number ticking over does not shove
    everything beside it sideways."""
    return "  ".join(
        pad(f"{mark} {count} {label}", COUNTER_CELL)
        for mark, count, label in _marks(stats, theme)
    )


def _short_counters(stats: Stats, theme: Theme) -> str:
    """The same numbers without their words, for a bar with no room for prose."""
    return "  ".join(f"{mark} {count}" for mark, count, _ in _marks(stats, theme))


SPEED_CELL = 16
COUNTER_CELL = 12
TAIL_CELL = 10


def render_status(
    stats: Stats, history: list[int], theme: Theme, width: int, sort_label: str = ""
) -> str:
    """Lay the bar out to a budget: the graph gives up its width first, since
    the numbers beside it are the part worth reading.

    The sort badge lives here rather than under the list so that changing the
    order never displaces the key legend.
    """
    speed = f"{glyph('🚀', theme.icons)} {human_speed(stats.speed)}"
    tail = f"{glyph('⏱', theme.icons)} {human_duration(stats.elapsed)}"
    badge = f"{glyph('⇅', theme.icons)} {sort_label}   " if sort_label else ""

    counts, mark, tail, roomy = _fit(stats, theme, width, badge, tail, speed)

    speed_cell = min(SPEED_CELL, max(cells(speed), width))
    tail_cell = TAIL_CELL if tail else 0
    fixed = speed_cell + cells(counts) + cells(mark) + tail_cell + (3 if counts else 0)
    # Whatever is left over, but never at the cost of a word: the layout above
    # was chosen without the graph in mind, so this only fills space nothing
    # else wanted. With the counters gone there is nothing to annotate.
    graph_width = max(0, min(40, width - fixed - 3)) if roomy else 0
    if graph_width < 8:
        graph_width = 0

    gap = " " * (3 if graph_width else 0)
    graph = (
        (_graph(history, theme, graph_width) if any(history) else " " * graph_width)
        if graph_width
        else ""
    )
    between = "   " if counts else ""
    if theme.mono:
        plain = sparkline(history, graph_width) if any(history) else " " * graph_width
        return f"{pad(speed, speed_cell)}{plain}{gap}{counts}{between}{mark}{tail}"
    return (
        f"[{theme.accent}]{pad(speed, speed_cell)}[/]{graph}{gap}"
        f"[{theme.dim}]{counts}[/]{between}[{theme.accent}]{mark}[/]"
        f"[{theme.dim}]{rpad(tail, tail_cell)}[/]"
    )


def _fit(stats: Stats, theme: Theme, width: int, badge: str, tail: str, speed: str):
    """What still fits, given up in order of what can be spared.

    The graph goes first, then the sort badge, then the counters' words, then
    how long dl has been open, then the counters entirely. The speed is the
    last thing standing: it is the reading the bar exists for.
    """
    full = _counters(stats, theme)
    short = _short_counters(stats, theme)
    room = width - min(SPEED_CELL, max(cells(speed), width))
    for counts, keep_badge, keep_tail in (
        (full, True, True),
        (full, False, True),
        (short, False, True),
        (short, False, False),
        ("", False, False),
    ):
        mark = badge if keep_badge else ""
        end = tail if keep_tail else ""
        cost = cells(counts) + cells(mark) + (TAIL_CELL if end else 0) + (3 if counts else 0)
        if cost <= room:
            return counts, mark, end, bool(counts)
    return "", "", "", False


class StatusBar(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.history: list[int] = []
        self.sort_label = ""
        self.stats: Stats | None = None

    def update_stats(self, stats: Stats, sort_label: str = "") -> None:
        self.history = (self.history + [stats.speed])[-40:]
        self.stats = stats
        self.sort_label = sort_label
        self._paint()

    def set_sort(self, sort_label: str) -> None:
        """The bar redraws twice a second; a keypress should not wait for it."""
        self.sort_label = sort_label
        if self.stats is not None:
            self._paint()

    def _paint(self) -> None:
        self.update(
            render_status(
                self.stats,
                self.history,
                self.theme_data,
                self.size.width or 100,
                self.sort_label,
            )
        )
