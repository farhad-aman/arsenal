import shlex
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import config, daemon, history, routing
from .config import STATE_DIR, Config
from .rpc import Aria2


def _first_file(status: dict) -> dict:
    files = status.get("files") or []
    return files[0] if files else {}


def _first_uri(status: dict) -> str:
    uris = _first_file(status).get("uris") or []
    return uris[0].get("uri", "") if uris else ""


def went_through_proxy(options: dict) -> bool:
    return bool(options.get("all-proxy") or options.get("http-proxy"))


def build_record(status: dict, mode: str, cfg: Config, proxied: bool = False) -> dict:
    raw_path = _first_file(status).get("path", "")
    url = _first_uri(status)
    path = Path(raw_path) if raw_path else None
    name = path.name if path else routing.filename_from_url(url)
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
        record["error"] = status.get("errorMessage") or f"code {status.get('errorCode', '?')}"
    return record


def drop_control_file(path: Path) -> bool:
    """aria2 leaves <file>.aria2 behind on completion in some configurations,
    and relocating the download would strand it in the old directory."""
    control = path.with_name(path.name + ".aria2")
    try:
        control.unlink()
        return True
    except OSError:
        return False


def relocate(path: Path, cfg: Config, url: str) -> Path:
    """Correct the destination when the real filename routes elsewhere.

    A file sitting outside the directory URL-based routing chose was pinned by
    -d or the picker, so it is left alone.
    """
    if not path.exists():
        return path
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


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    state = STATE_DIR
    if not args:
        return 0
    if args[0] == "idle":
        return _run_idle(int(args[1]), int(args[2]), Path(args[3]))

    try:
        mode = args[0]
        gid = args[1] if len(args) > 1 else ""
        cfg = config.load()
        client = daemon.ensure_running(cfg, state)
        status = client.tell_status(gid)
        try:
            options = client.get_option(gid)
        except Exception:
            options = {}
        record = build_record(status, mode, cfg, went_through_proxy(options))
        if mode == "complete" and record["path"]:
            original = Path(record["path"])
            drop_control_file(original)
            final = relocate(original, cfg, record["url"])
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
