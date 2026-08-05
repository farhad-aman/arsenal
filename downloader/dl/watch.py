import shutil
import subprocess
import time
from collections import deque
from pathlib import Path

from . import cli, duplicates, history, routing, youtube
from .config import STATE_DIR, Config
from .rpc import Aria2Error, Aria2Unreachable

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


def _in_flight(client) -> list[dict]:
    try:
        return list(client.tell_active()) + list(client.tell_waiting())
    except (Aria2Error, Aria2Unreachable):
        return []


def _already(collision: duplicates.Collision) -> str:
    return "already downloading" if collision.in_flight else "already there"


def poll_once(text: str, seen: deque, cfg: Config, client) -> bool:
    value = text.strip()
    if not is_downloadable(value) or value in seen:
        return False
    seen.append(value)
    if youtube.is_youtube(value):
        return _catch_youtube(value, cfg)

    name = routing.filename_from_url(value)
    resolution = routing.resolve(value, name, cfg)
    target = resolution.path / name if name else None
    collision = duplicates.detect(
        value, target, history.tail(STATE_DIR / "history.jsonl", 200), _in_flight(client)
    )
    if collision is not None:
        # Nothing here can prompt, so the choice a duplicate deserves has to be
        # deferred rather than guessed at.
        print(f"  ⏭  skipped  {name or value}  — {_already(collision)}; `dl <url>` to choose")
        return False

    resolution.path.mkdir(parents=True, exist_ok=True)
    proxied = routing.through_proxy(value, cfg)
    sent = routing.header_lines(routing.headers_for(value, cfg))
    client.add_uri([value], cli.add_options(cfg, resolution, proxied, None, sent))
    via = "  🌐 via proxy" if proxied else ""
    print(f"  {resolution.category.icon} caught  {name or value}  →  {resolution.path}{via}")
    return True


def _catch_youtube(url: str, cfg: Config) -> bool:
    """aria2 cannot resolve a watch page into streams — left to it, a caught
    YouTube link is saved as the HTML of the page."""
    if shutil.which("yt-dlp") is None:
        print(f"  ⚠  skipped  {url}  — yt-dlp not found")
        return False

    from . import ytjob, ytrun
    from .tui import ytflow

    category = cfg.categories.get("video") or routing.OTHER
    job = ytjob.new_job(
        url,
        category.dir,
        youtube.DEFAULTS,
        cfg.proxy if routing.through_proxy(url, cfg) else "",
        cfg.cookies_from,
    )
    print(f"  ⏳ asking YouTube about  {url}")
    try:
        title, filename, total = ytrun.probe(job, cfg.probe_timeout)
    except ytrun.ProbeFailed as exc:
        # Queuing blind would let yt-dlp find the file already there and do
        # nothing, which is exactly the silence this check exists to remove.
        print(f"  ⏭  skipped  {url}  — {exc}; `dl <url>` to choose")
        return False
    target = Path(filename) if filename else None
    if target is not None and duplicates.detect_target(target) is not None:
        print(f"  ⏭  skipped  {target.name}  — already there; `dl <url>` to choose")
        return False

    job["title"] = title
    job["total"] = total
    ytflow.spawn(job)
    via = "  🌐 via proxy" if job["proxy"] else ""
    print(f"  {category.icon} caught  {title or url}  →  {category.dir}{via}")
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
    print("  YouTube links are taken at best quality; run `dl <url>` to choose")
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
