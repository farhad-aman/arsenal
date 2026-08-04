import sys
import time
from pathlib import Path

from . import duplicates, routing
from .config import Config, parse_rate
from .destinations import ensure_writable
from .format import human_bytes, human_speed
from .routing import Resolution
from .rpc import Aria2Error, Aria2Unreachable

SCHEMES = ("http://", "https://", "ftp://", "ftps://", "sftp://", "magnet:")
_SETTLED = ("removed", "error", "complete")


def looks_like_url(value: str) -> bool:
    return value.startswith(SCHEMES) or value.endswith(".torrent")


def add_options(
    cfg: Config, resolution: Resolution, proxy: bool = False, decision: str | None = None
) -> dict:
    options = {
        "dir": str(resolution.path),
        "max-connection-per-server": str(cfg.limits.connections),
        "split": str(cfg.limits.splits),
        "min-split-size": cfg.limits.min_split,
        "max-download-limit": cfg.limits.per_download,
    }
    if proxy:
        options["all-proxy"] = cfg.proxy
    if decision == duplicates.RENAME:
        options["auto-file-renaming"] = "true"
        options["allow-overwrite"] = "false"
        # --continue makes aria2 resume *into* the existing file instead of
        # renaming, which silently destroys the copy rename is meant to keep.
        options["continue"] = "false"
    elif decision == duplicates.OVERWRITE:
        options["auto-file-renaming"] = "false"
        options["allow-overwrite"] = "true"
    return options


def _unlink(path: Path) -> None:
    for target in (path, path.with_name(path.name + ".aria2")):
        try:
            target.unlink()
        except OSError:
            pass


def evict(client, target: Path, timeout: float = 5.0) -> str:
    """Clear the way for an overwrite: drop any download still writing to
    `target` from the queue, then delete what it left behind.

    aria2 rewrites the control file while winding a download down, so the
    unlink has to wait for the removal to settle or the .aria2 comes back.
    """
    in_flight = list(client.tell_active()) + list(client.tell_waiting())
    victim = next((row for row in in_flight if duplicates.path_of(row) == target), None)
    gid = victim.get("gid", "") if victim else ""
    if gid:
        try:
            client.remove(gid)
        except (Aria2Error, Aria2Unreachable):
            pass
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if client.tell_status(gid).get("status", "") in _SETTLED:
                    break
            except (Aria2Error, Aria2Unreachable):
                break
            time.sleep(0.05)
    _unlink(target)
    return gid


def cmd_add(
    urls: list[str],
    cfg: Config,
    client,
    explicit_dir: Path | None,
    chosen: list[Path | None] | None = None,
    proxy: bool = False,
    decisions: list[str | None] | None = None,
) -> tuple[int, list[str]]:
    if not urls:
        print("dl: no URLs given", file=sys.stderr)
        return 1, []
    bad = [u for u in urls if not looks_like_url(u)]
    if bad:
        for value in bad:
            print(f"dl: not a URL: {value!r}", file=sys.stderr)
        print("dl: run `dl --help` for usage", file=sys.stderr)
        return 1, []
    failures = 0
    gids: list[str] = []
    for index, url in enumerate(urls):
        name = routing.filename_from_url(url)
        routed = routing.resolve(url, name, cfg)
        pick = chosen[index] if chosen and index < len(chosen) else None
        target = pick or explicit_dir or routed.path
        resolution = Resolution(Path(target), routed.category)
        decision = decisions[index] if decisions and index < len(decisions) else None
        if decision == duplicates.SKIP:
            print(f"  ⏭  skipped  {name or url}  — already there")
            continue
        if not ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        if decision == duplicates.OVERWRITE:
            evict(client, resolution.path / name if name else resolution.path)
        gids.append(client.add_uri([url], add_options(cfg, resolution, proxy, decision)))
        via = "  🌐 via proxy" if proxy else ""
        replaced = "  ♻️ overwriting" if decision == duplicates.OVERWRITE else ""
        print(
            f"  {resolution.category.icon} queued  {name or url}"
            f"  →  {resolution.path}{via}{replaced}"
        )
    return (1 if failures else 0), gids


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
