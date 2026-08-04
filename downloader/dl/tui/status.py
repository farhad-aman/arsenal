from dataclasses import dataclass

from textual.widgets import Static

from ..format import human_duration, human_speed, sparkline
from ..theme import Theme, ramp_color


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


def render_status(stats: Stats, history: list[int], theme: Theme, width: int) -> str:
    graph_width = 40 if width >= 90 else (20 if width >= 66 else 10)
    speed = human_speed(stats.speed)
    counts = f"↓{stats.active}  ⏳{stats.waiting}  ✅{stats.done}"
    tail = f"⏱ {human_duration(stats.elapsed)}"
    if theme.mono:
        return f"{speed}   {sparkline(history, graph_width)}   {counts}   {tail}"
    return (
        f"[{theme.accent}]🚀 {speed}[/]   {_graph(history, theme, graph_width)}   "
        f"[{theme.dim}]{counts}[/]   [{theme.dim}]{tail}[/]"
    )


class StatusBar(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.history: list[int] = []

    def update_stats(self, stats: Stats) -> None:
        self.history = (self.history + [stats.speed])[-40:]
        self.update(render_status(stats, self.history, self.theme_data, self.size.width or 100))
