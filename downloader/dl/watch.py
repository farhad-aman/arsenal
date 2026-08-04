import subprocess
import time
from collections import deque

from . import cli, routing
from .config import Config

SCHEMES = ("http://", "https://", "ftp://", "magnet:")


def is_downloadable(text: str) -> bool:
    value = text.strip()
    if not value or len(value.split()) != 1:
        return False
    return value.startswith(SCHEMES)


def read_clipboard() -> str:
    try:
        return subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False, timeout=2
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def poll_once(text: str, seen: deque, cfg: Config, client) -> bool:
    value = text.strip()
    if not is_downloadable(value) or value in seen:
        return False
    seen.append(value)
    name = routing.filename_from_url(value)
    resolution = routing.resolve(value, name, cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    client.add_uri([value], cli.add_options(cfg, resolution))
    print(f"  {resolution.category.icon} caught  {name or value}  →  {resolution.path}")
    return True


def run(
    cfg: Config,
    client,
    interval: float = 0.8,
    reader=None,
    iterations: int | None = None,
) -> int:
    source = reader or read_clipboard
    seen: deque = deque(maxlen=20)
    print("  watching clipboard — Ctrl-C to stop")
    count = 0
    try:
        while iterations is None or count < iterations:
            poll_once(source(), seen, cfg, client)
            count += 1
            if interval:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0
