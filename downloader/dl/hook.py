import shlex
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import checksum, config, daemon, history, routing, torrent
from .config import STATE_DIR, Config
from .rpc import Aria2, Aria2Error, Aria2Unreachable


def _first_file(status: dict) -> dict:
    files = status.get("files") or []
    return files[0] if files else {}


def _first_uri(status: dict) -> str:
    uris = _first_file(status).get("uris") or []
    return uris[0].get("uri", "") if uris else ""


def went_through_proxy(options: dict) -> bool:
    return bool(options.get("all-proxy") or options.get("http-proxy"))


def skip_record(status: dict) -> bool:
    """A magnet's metadata download completes in seconds and hands off to the
    real transfer. Recording it would put a row named after a hash into the
    history for every magnet ever added."""
    return torrent.is_metadata(status)


def build_record(status: dict, mode: str, cfg: Config, proxied: bool = False) -> dict:
    raw_path = _first_file(status).get("path", "")
    url = _first_uri(status)
    path = Path(raw_path) if raw_path else None
    name = path.name if path else routing.filename_from_url(url)
    if torrent.is_torrent_status(status):
        # One file lands as that file, several as a folder — either way the
        # torrent names itself, and the folder is what was downloaded.
        path = torrent.target(status, Path(status.get("dir", "") or ""))
        name = torrent.name_of(status) or name
    total = int(status.get("totalLength", 0) or 0)
    speed = int(status.get("downloadSpeed", 0) or 0)
    resolution = routing.resolve(url, name, cfg)
    record = {
        "ts": int(time.time()),
        "name": name,
        "bytes": total,
        "seconds": 0,
        "avg_bps": max(speed, 0),
        "path": str(path) if path else "",
        "category": resolution.category.name,
        "url": url,
        "status": "ok" if mode == "complete" else "error",
        "proxy": proxied,
    }
    if mode != "complete":
        record["error"] = (
            checksum.explain(status) or f"code {status.get('errorCode', '?')}"
        )
    return record


def discard_corrupt(status: dict) -> bool:
    """Throw away a download that failed its checksum.

    The bytes are provably not what was asked for, and aria2 leaves them
    complete on disk with their control file — so a retry resumes a finished
    download and changes nothing at all.
    """
    if not checksum.mismatched(status):
        return False
    raw = _first_file(status).get("path", "")
    if not raw:
        return False
    path = Path(raw)
    gone = False
    for target in (path, path.with_name(path.name + ".aria2")):
        try:
            target.unlink()
            gone = True
        except OSError:
            pass
    return gone


def drop_source_torrent(client, status: dict) -> bool:
    """Remove the .torrent that was fetched in order to start this download.

    Asking for a .torrent over http downloads it into the destination, where
    it then sits beside the thing it described. Nobody asked for the .torrent.
    """
    parent = status.get("following") or ""
    if not parent:
        return False
    try:
        origin = client.tell_status(parent)
    except (Aria2Error, Aria2Unreachable):
        return False
    files = origin.get("files") or [{}]
    raw = files[0].get("path", "") or ""
    # A magnet's parent is a [METADATA] placeholder with no file behind it.
    if not raw.lower().endswith(torrent.SUFFIX):
        return False
    try:
        Path(raw).unlink()
        return True
    except OSError:
        return False


def drop_control_file(path: Path) -> bool:
    """aria2 leaves <file>.aria2 behind on completion in some configurations,
    and relocating the download would strand it in the old directory."""
    if not path.name:
        return False
    control = path.with_name(path.name + ".aria2")
    try:
        control.unlink()
        return True
    except OSError:
        return False


def relocate(path: Path, cfg: Config, url: str, by_content: bool = False) -> Path:
    """Correct the destination when the real filename routes elsewhere.

    A file sitting outside the directory URL-based routing chose was pinned by
    -d or the picker, so it is left alone.

    by_content is for a torrent, whose destination was settled from a magnet or
    a .torrent filename before anyone knew what was inside. Comparing against
    URL routing there asks whether it is already where its own contents belong,
    which is the question this is supposed to answer — so the only thing that
    can have pinned it is -d, and that shows as anywhere but the default.
    """
    if not path.exists():
        return path
    if by_content:
        if path.parent != cfg.general.default_dir:
            return path
    else:
        routed = routing.resolve(url, routing.filename_from_url(url), cfg).path
        if path.parent != routed:
            return path
    target_dir = routing.resolve(url, path.name, cfg).path
    if target_dir == path.parent:
        return path
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / path.name
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        n = 2
        while destination.exists():
            destination = target_dir / f"{stem}.{n}{suffix}"
            n += 1
    path.replace(destination)
    return destination


def _applescript(value: str) -> str:
    """AppleScript string literals take double quotes. Python's !r gives single
    ones, which osascript rejects outright."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def notify_script(title: str, body: str) -> str:
    return f"display notification {_applescript(body)} with title {_applescript(title)}"


def notify(title: str, body: str) -> bool:
    done = subprocess.run(
        ["osascript", "-e", notify_script(title, body)], capture_output=True, check=False
    )
    return done.returncode == 0


def run_user_hook(cfg: Config, record: dict) -> str:
    """Run the user's on_complete command. Returns why it failed, or "".

    No shell. Names come off the internet, and one containing `;` or `$(...)`
    would otherwise be executed rather than passed along.

    Blocking is deliberate: the useful hooks unpack or move the file they were
    handed, so they need it to still be there and they need to not race each
    other. Nothing waits on this — aria2 never waits for a completion hook, and
    the YouTube path runs inside its own detached supervisor.
    """
    if not cfg.on_complete.strip():
        return ""
    argv = shlex.split(cfg.on_complete)
    argv[0] = str(Path(argv[0]).expanduser())
    argv += [record.get("path", ""), record.get("category", ""), record.get("url", "")]
    try:
        done = subprocess.run(
            argv, capture_output=True, text=True, timeout=cfg.hook_timeout, check=False
        )
    except subprocess.TimeoutExpired:
        return f"timed out after {cfg.hook_timeout}s"
    except OSError as exc:
        return str(exc)
    if done.returncode == 0:
        return ""
    said = (done.stderr or done.stdout or "").strip().splitlines()
    return said[-1][:200] if said else f"exited {done.returncode}"


def after_complete(cfg: Config, record: dict, state: Path) -> str:
    """Run the completion hook and make any failure findable.

    A hook that fails never fails the download: the bytes arrived, and what the
    user asked to happen afterwards is a separate thing that can go wrong.
    """
    problem = run_user_hook(cfg, record)
    if not problem:
        return ""
    _log(state, f"on_complete failed for {record.get('name', '')}: {problem}")
    if cfg.general.notify:
        notify("Download hook failed", f"{record.get('name', '')}: {problem}")
    return problem


def _spawn_sleeper(state: Path, generation: int, delay: int) -> None:
    subprocess.Popen(
        [sys.executable, "-m", "dl.hook", "idle", str(generation), str(delay), str(state)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def arm_idle_shutdown(client, cfg: Config, state: Path) -> bool:
    if client.tell_active() or client.tell_waiting():
        return False
    generation = daemon.bump_generation(state)
    _spawn_sleeper(state, generation, cfg.general.idle_timeout)
    return True


def _run_idle(generation: int, delay: int, state: Path) -> int:
    time.sleep(delay)
    if daemon.read_generation(state) != generation:
        return 0
    try:
        client = Aria2("127.0.0.1", daemon.read_port(state), daemon.read_secret(state), timeout=2.0)
        if not client.tell_active() and not client.tell_waiting():
            client.shutdown()
    except Exception:
        pass
    return 0


def _log(state: Path, message: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "hook.log", "a") as fh:
        fh.write(f"--- {time.ctime()}\n{message}\n")


def _log_failure(state: Path) -> None:
    _log(state, traceback.format_exc())


def _take(args: list[str], flag: str) -> str:
    """Pull `--flag value` out of the argument list, if it is there.

    aria2 appends its own gid, path and file count after whatever the shim
    passed, so the flags cannot simply be positional.
    """
    if flag not in args:
        return ""
    at = args.index(flag)
    value = args[at + 1] if at + 1 < len(args) else ""
    del args[at : at + 2]
    return value


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        return 0
    if args[0] == "idle":
        return _run_idle(int(args[1]), int(args[2]), Path(args[3]))

    # The daemon tells its hook where to write, because nothing in the
    # environment can move dl's state any more.
    where = _take(args, "--state")
    config_file = _take(args, "--config")
    state = Path(where) if where else STATE_DIR

    try:
        mode = args[0]
        gid = args[1] if len(args) > 1 else ""
        cfg = config.load(Path(config_file)) if config_file else config.load()
        # Talk to the daemon that called us, which is the one this state
        # directory records. ensure_running would go to the fixed port and
        # could start a daemon — from inside a hook, of one that is running.
        client = Aria2(
            "127.0.0.1", daemon.read_port(state), daemon.read_secret(state)
        )
        status = client.tell_status(gid)
        try:
            options = client.get_option(gid)
        except Exception:
            options = {}
        if skip_record(status):
            arm_idle_shutdown(client, cfg, state)
            return 0
        record = build_record(status, mode, cfg, went_through_proxy(options))
        if discard_corrupt(status):
            record["error"] = f"{checksum.MISMATCH} — the file was removed"
            record["path"] = ""
        if mode == "complete" and record["path"]:
            original = Path(record["path"])
            drop_control_file(original)
            drop_source_torrent(client, status)
            final = relocate(
                original, cfg, record["url"], by_content=torrent.is_torrent_status(status)
            )
            drop_control_file(final)
            record["path"] = str(final)
        history.append(record, state / "history.jsonl")
        if cfg.general.notify:
            title = "Download complete" if mode == "complete" else "Download failed"
            notify(title, record["name"] or gid)
        if mode == "complete":
            after_complete(cfg, record, state)
        arm_idle_shutdown(client, cfg, state)
    except Exception:
        _log_failure(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
