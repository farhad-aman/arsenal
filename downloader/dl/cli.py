import os
import sys
from pathlib import Path

from . import routing
from .config import Config, parse_rate
from .format import human_bytes, human_speed
from .routing import Resolution


def add_options(cfg: Config, resolution: Resolution) -> dict:
    return {
        "dir": str(resolution.path),
        "max-connection-per-server": str(cfg.limits.connections),
        "split": str(cfg.limits.splits),
        "min-split-size": cfg.limits.min_split,
        "max-download-limit": cfg.limits.per_download,
    }


def _ensure_writable(target: Path) -> bool:
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(target, os.W_OK)


def cmd_add(urls: list[str], cfg: Config, client, explicit_dir: Path | None) -> int:
    if not urls:
        print("dl: no URLs given", file=sys.stderr)
        return 1
    failures = 0
    for url in urls:
        name = routing.filename_from_url(url)
        resolution = routing.resolve(url, name, cfg, explicit_dir)
        if not _ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        client.add_uri([url], add_options(cfg, resolution))
        print(f"  {resolution.category.icon} queued  {name or url}  →  {resolution.path}")
    return 1 if failures else 0


def _rows(client) -> list[dict]:
    return list(client.tell_active()) + list(client.tell_waiting()) + list(client.tell_stopped())


def cmd_ls(cfg: Config, client, use_color: bool) -> int:
    for item in _rows(client):
        total = int(item.get("totalLength", 0) or 0)
        done = int(item.get("completedLength", 0) or 0)
        pct = int(done * 100 / total) if total else 0
        files = item.get("files") or [{}]
        name = Path(files[0].get("path", "")).name or "(pending)"
        print(
            f"{item.get('gid', ''):<18} {item.get('status', ''):<9} {pct:>3}% "
            f"{human_bytes(total):>10} {human_speed(int(item.get('downloadSpeed', 0) or 0)):>12}  {name}"
        )
    return 0


def _gids(client, source: str) -> list[str]:
    if source == "active":
        return [i["gid"] for i in client.tell_active()]
    return [i["gid"] for i in client.tell_waiting()]


def cmd_pause(target: str, client) -> int:
    gids = _gids(client, "active") if target == "all" else [target]
    for gid in gids:
        client.pause(gid)
    return 0


def cmd_resume(target: str, client) -> int:
    gids = _gids(client, "waiting") if target == "all" else [target]
    for gid in gids:
        client.unpause(gid)
    return 0


def cmd_rm(target: str, client) -> int:
    client.remove(target)
    return 0


def cmd_limit(rate: str, cfg: Config, client) -> int:
    value = parse_rate(rate)
    client.change_global_option({"max-overall-download-limit": value})
    print(f"  limit {'off' if value == '0' else value}")
    return 0


def cmd_kill(client) -> int:
    client.shutdown()
    print("  daemon stopped")
    return 0


def read_url_file(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
