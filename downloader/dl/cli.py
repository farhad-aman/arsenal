import json
import sys
import time
from pathlib import Path

from . import duplicates, routing, search, theme, torrent
from .config import Config
from .destinations import ensure_writable
from .format import human_bytes, human_speed
from .routing import Resolution
from .rpc import Aria2Error, Aria2Unreachable
from .theme import glyph

SCHEMES = ("http://", "https://", "ftp://", "ftps://", "sftp://", "magnet:")
_SETTLED = ("removed", "error", "complete")


def looks_like_url(value: str) -> bool:
    return value.startswith(SCHEMES) or value.endswith(".torrent")


def add_options(
    cfg: Config,
    resolution: Resolution,
    proxy: bool = False,
    decision: str | None = None,
    headers: list[str] | None = None,
    digest: str = "",
) -> dict:
    options = {
        "dir": str(resolution.path),
        "max-connection-per-server": str(cfg.limits.connections),
        "split": str(cfg.limits.splits),
        "min-split-size": cfg.limits.min_split,
        "max-download-limit": cfg.limits.per_download,
        # Per download rather than left to the daemon: dl adopts a running
        # daemon rather than restarting it, so one started before torrents
        # existed would seed to 1:1 and sit at 100% looking stuck.
        "seed-time": "0",
    }
    if digest:
        options["checksum"] = digest
    if proxy:
        options["all-proxy"] = cfg.proxy
    if headers:
        # Sent over RPC rather than on a command line, so a Cookie or
        # Authorization value never shows up in `ps`.
        options["header"] = list(headers)
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
    # Path("") is PosixPath(".") and truthy, so a download aria2 has not named
    # yet arrives here looking like a real path and with_name() raises on it.
    if not path.name:
        return
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
    if not target.name:
        return ""
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
    headers: list[str] | None = None,
    digest: str = "",
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
    resolved_theme = theme.select(cfg)
    icons = resolved_theme.icons
    for index, url in enumerate(urls):
        name = routing.filename_from_url(url)
        routed = routing.resolve(url, name, cfg)
        pick = chosen[index] if chosen and index < len(chosen) else None
        target = pick or explicit_dir or routed.path
        resolution = Resolution(Path(target), routed.category)
        decision = decisions[index] if decisions and index < len(decisions) else None
        if decision == duplicates.SKIP:
            print(f"  {glyph('⏭', icons)}  skipped  {name or url}  — already there")
            continue
        if not ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        if decision == duplicates.OVERWRITE:
            evict(client, resolution.path / name if name else resolution.path)
        via_proxy = routing.through_proxy(url, cfg, forced=proxy)
        sent = routing.header_lines(routing.headers_for(url, cfg)) + list(headers or [])
        options = add_options(cfg, resolution, via_proxy, decision, sent, digest)
        if torrent.is_torrent_file(url):
            # Already on disk, so there is nowhere for aria2 to fetch it from.
            gids.append(client.add_torrent(Path(url).expanduser(), options))
        else:
            gids.append(client.add_uri([url], options))
        via = f"  {glyph('🌐', icons)} via proxy" if via_proxy else ""
        replaced = (
            f"  {glyph('♻️', icons)} overwriting" if decision == duplicates.OVERWRITE else ""
        )
        print(
            f"  {theme.icon_for(resolution.category, resolved_theme)} queued  {name or url}"
            f"  →  {resolution.path}{via}{replaced}"
        )
    return (1 if failures else 0), gids


# Of the stopped list, only failures. A removal lingers there for the life of
# the daemon, and a finished download is in `dl history`, which outlives it.
LISTED_WHEN_STOPPED = ("error",)


def _name_of(item: dict) -> str:
    """What to call a download.

    A multi-file torrent is a folder of parts, so its first file names a
    fragment rather than the thing that was asked for.
    """
    if torrent.is_torrent_status(item):
        named = torrent.name_of(item)
        if named:
            return named
    files = item.get("files") or [{}]
    return Path(files[0].get("path", "")).name


def _rows(client) -> list[dict]:
    stopped = [
        item for item in client.tell_stopped() if item.get("status") in LISTED_WHEN_STOPPED
    ]
    return list(client.tell_active()) + list(client.tell_waiting()) + stopped


def _proxy_badge(client, gid: str, cfg: Config) -> str:
    """Whether a download is proxied lives in its options, which the status
    call does not carry, so it costs a second round trip per row."""
    from .hook import went_through_proxy

    try:
        options = client.get_option(gid)
    except (Aria2Error, Aria2Unreachable, AttributeError):
        return ""
    if not went_through_proxy(options):
        return ""
    return "  " + glyph("🌐", theme.icons_on(cfg))


def _proxied(client, gid: str) -> bool:
    from .hook import went_through_proxy

    try:
        return went_through_proxy(client.get_option(gid))
    except (Aria2Error, Aria2Unreachable, AttributeError):
        return False


def ls_record(item: dict, client, cfg: Config) -> dict:
    """One live download, as the shape `dl ls --json` prints.

    Deliberately not aria2's own status: that carries several dozen fields of
    which most are internal, and this has to stay readable a year from now.
    """
    total = int(item.get("totalLength", 0) or 0)
    done = int(item.get("completedLength", 0) or 0)
    files = item.get("files") or [{}]
    path = files[0].get("path", "") or ""
    uris = files[0].get("uris") or []
    url = uris[0].get("uri", "") if uris else ""
    name = _name_of(item)
    return {
        "gid": item.get("gid", ""),
        "status": item.get("status", ""),
        "name": name,
        "percent": int(done * 100 / total) if total else 0,
        "total": total,
        "completed": done,
        "speed": int(item.get("downloadSpeed", 0) or 0),
        "connections": int(item.get("connections", 0) or 0),
        "category": routing.resolve(url, name, cfg).category.name,
        "path": path,
        "url": url,
        "proxy": _proxied(client, item.get("gid", "")),
    }


def cmd_ls(
    cfg: Config, client, use_color: bool, query: str = "", as_json: bool = False
) -> int:
    for item in search.keep(_rows(client), query, _name_of):
        if as_json:
            print(json.dumps(ls_record(item, client, cfg), ensure_ascii=False))
            continue
        total = int(item.get("totalLength", 0) or 0)
        done = int(item.get("completedLength", 0) or 0)
        pct = int(done * 100 / total) if total else 0
        name = _name_of(item) or "(pending)"
        # The badge goes last so every existing column keeps its position and
        # `dl ls | grep paused` still works.
        via = _proxy_badge(client, item.get("gid", ""), cfg)
        print(
            f"{item.get('gid', ''):<18} {item.get('status', ''):<9} {pct:>3}% "
            f"{human_bytes(total):>10} {human_speed(int(item.get('downloadSpeed', 0) or 0)):>12}  {name}{via}"
        )
    return 0


HISTORY_DEFAULT = 20


def history_line(record: dict, cfg: Config) -> str:
    """One finished download, as a line that sorts and greps.

    Only the leading columns are padded. Names here are as often Persian as
    ASCII, and padding a string of double-width or right-to-left characters
    lands the rest of the line somewhere different on every row.
    """
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(int(record.get("ts", 0) or 0)))
    ok = record.get("status") == "ok"
    state = "ok   " if ok else "error"
    size = human_bytes(int(record.get("bytes", 0) or 0))
    category = record.get("category", "") or ""
    name = record.get("name", "") or "(unnamed)"
    line = f"{when}  {state}  {size:>10}  {category:<8}  {name}"
    where = Path(record.get("path", "") or "").parent
    if str(where) not in ("", "."):
        line += f"  →  {where}"
    if record.get("proxy"):
        line += "  " + glyph("🌐", theme.icons_on(cfg))
    if not ok and record.get("error"):
        line += f"  — {record['error']}"
    return line


def cmd_history(cfg: Config, log: Path, args: list[str]) -> int:
    import json as _json

    wanted = [a for a in args if not a.startswith("-")]
    counts = [a for a in wanted if a.isdigit()]
    query = " ".join(a for a in wanted if not a.isdigit())
    if len(counts) > 1:
        print(f"dl: history takes one count, not {' and '.join(counts)}", file=sys.stderr)
        return 1
    count = int(counts[0]) if counts else HISTORY_DEFAULT

    from . import history

    # Filtering happens after the read, so `--failed 5` means five failures
    # rather than however many appear in the last five downloads. A query reads
    # the whole log for the same reason: the match may be older than `count`.
    reach = max(count * 20, count) if "--failed" in args else count
    records = history.find(log, query, reach) if query else history.tail(log, reach)
    if "--failed" in args:
        records = [r for r in records if r.get("status") != "ok"]
    records = records[::-1][:count]

    if not records:
        print(f'  nothing found matching "{query}"' if query else "  nothing in the download history yet")
        return 0
    for record in records:
        print(_json.dumps(record, ensure_ascii=False) if "--json" in args else history_line(record, cfg))
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
    forget_result(client, target)
    return 0


def forget_result(client, gid: str) -> None:
    """Clear the stopped-list entry a removal leaves behind.

    aria2 refuses when there is no result yet, which is not a failure: the
    download is gone either way.
    """
    try:
        client.remove_download_result(gid)
    except (Aria2Error, Aria2Unreachable, AttributeError, TypeError):
        pass


def cmd_kill(client, state=None) -> int:
    client.shutdown()
    if state is not None:
        from . import daemon

        daemon.clear_pid(state)
    print("  daemon stopped")
    return 0


def cmd_strays(state, confirm=None) -> int:
    """Show what is listening on the ports older versions could roam to.

    None of these can be reached — they hold a secret no state directory has —
    so the only thing to do with one is stop it, and only after it has been
    named.
    """
    from . import daemon

    found = daemon.strays(state)
    if not found:
        print("  no stray daemons")
        return 0
    print(f"  {len(found)} stray daemon{'s' if len(found) > 1 else ''}, unreachable by dl:")
    for pid, port in found:
        print(f"    pid {pid}  on port {port}   (downloads it holds cannot be listed)")
    ask = confirm if confirm is not None else _ask_yes
    if not ask("  stop them? [y/N] "):
        print("  left alone")
        return 0
    stopped = daemon.stop_strays(found)
    print(f"  stopped {stopped} of {len(found)}")
    return 0


def _ask_yes(prompt: str) -> bool:
    try:
        return input(prompt).strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


def read_url_file(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
