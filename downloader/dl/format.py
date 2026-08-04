from collections.abc import Sequence

BLOCKS = "▁▂▃▄▅▆▇█"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
DASH = "—"

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(n: int) -> str:
    if n < 0:
        return DASH
    value = float(n)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
        value /= 1024
    return DASH


def human_speed(bps: int) -> str:
    """Always keeps one decimal above B/s — speed fluctuates, so 12.4 reads
    better than 12, unlike the static sizes human_bytes formats."""
    value = float(max(bps, 0))
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            return f"{int(value)} B/s" if unit == "B" else f"{value:.1f} {unit}/s"
        value /= 1024
    return DASH


def human_duration(seconds: int) -> str:
    if seconds < 0:
        return DASH
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def sparkline(samples: Sequence[int], width: int) -> str:
    if width <= 0:
        return ""
    window = list(samples)[-width:]
    window = [0] * (width - len(window)) + window
    peak = max(window)
    if peak <= 0:
        return BLOCKS[0] * width
    return "".join(BLOCKS[min(len(BLOCKS) - 1, v * (len(BLOCKS) - 1) // peak)] for v in window)


def progress_bar(pct: float, width: int) -> str:
    if width <= 0:
        return ""
    ratio = min(max(pct, 0.0), 100.0) / 100.0
    body = int(ratio * width)
    if body >= width:
        return "█" * width
    if body <= 0:
        return "░" * width
    comet = "▓▒░"[: width - body]
    return "█" * body + comet + "░" * (width - body - len(comet))
