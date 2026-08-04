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


def build_record(status: dict, mode: str, cfg: Config) -> dict:
    path = Path(_first_file(status).get("path", ""))
    url = _first_uri(status)
    name = path.name
    total = int(status.get("totalLength", 0) or 0)
    speed = int(status.get("downloadSpeed", 0) or 0)
    resolution = routing.resolve(url, name, cfg)
    record = {
        "ts": int(time.time()),
        "name": name,
        "bytes": total,
        "seconds": 0,
        "avg_bps": max(speed, 0),
        "path": str(path),
        "category": resolution.category.name,
        "url": url,
        "status": "ok" if mode == "complete" else "error",
    }
    if mode != "complete":
        record["error"] = status.get("errorMessage") or f"code {status.get('errorCode', '?')}"
    return record


def relocate(path: Path, cfg: Config, url: str) -> Path:
    if not path.exists():
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


def notify(title: str, body: str) -> None:
    script = f'display notification {body!r} with title {title!r}'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


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


def _log_failure(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "hook.log", "a") as fh:
        fh.write(f"--- {time.ctime()}\n{traceback.format_exc()}\n")


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
        record = build_record(status, mode, cfg)
        if mode == "complete" and record["path"]:
            record["path"] = str(relocate(Path(record["path"]), cfg, record["url"]))
        history.append(record, state / "history.jsonl")
        if cfg.general.notify:
            title = "Download complete" if mode == "complete" else "Download failed"
            notify(title, record["name"] or gid)
        arm_idle_shutdown(client, cfg, state)
    except Exception:
        _log_failure(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
