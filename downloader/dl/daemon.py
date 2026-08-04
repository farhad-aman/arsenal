import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import STATE_DIR, Config
from .rpc import Aria2, Aria2Error, Aria2Unreachable

PORT_RANGE = range(6810, 6820)
_SHIM = '#!/bin/sh\nexec env DL_STATE_DIR={state} {python} -m dl.hook {mode} "$@"\n'


class Aria2Missing(Exception):
    pass


class DaemonStartFailed(Exception):
    pass


def read_secret(state: Path) -> str:
    state.mkdir(parents=True, exist_ok=True)
    target = state / "rpc.secret"
    if not target.exists():
        target.write_text(secrets.token_urlsafe(32))
        target.chmod(0o600)
    return target.read_text().strip()


def read_port(state: Path) -> int:
    target = state / "port"
    try:
        return int(target.read_text().strip())
    except (OSError, ValueError):
        return PORT_RANGE.start


def write_port(state: Path, port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "port").write_text(str(port))


def write_hook_shims(state: Path, python: str) -> tuple[Path, Path]:
    hooks = state / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    written = []
    for mode in ("complete", "error"):
        target = hooks / f"{mode}.sh"
        target.write_text(_SHIM.format(python=python, mode=mode, state=state))
        target.chmod(0o755)
        written.append(target)
    return written[0], written[1]


def read_generation(state: Path) -> int:
    try:
        return int((state / "generation").read_text().strip())
    except (OSError, ValueError):
        return 0


def bump_generation(state: Path) -> int:
    state.mkdir(parents=True, exist_ok=True)
    value = read_generation(state) + 1
    (state / "generation").write_text(str(value))
    return value


def quarantine_session(state: Path) -> None:
    session = state / "session"
    if session.exists():
        session.replace(state / "session.bad")


def aria2_args(cfg: Config, state: Path, port: int, secret: str) -> list[str]:
    complete, error = write_hook_shims(state, sys.executable)
    args = [
        "aria2c",
        "--enable-rpc",
        "--rpc-listen-all=false",
        f"--rpc-listen-port={port}",
        f"--rpc-secret={secret}",
        "--continue=true",
        "--auto-file-renaming=true",
        "--allow-overwrite=false",
        "--max-tries=5",
        "--retry-wait=3",
        "--daemon=false",
        f"--max-concurrent-downloads={cfg.general.max_concurrent}",
        f"--max-connection-per-server={cfg.limits.connections}",
        f"--split={cfg.limits.splits}",
        f"--min-split-size={cfg.limits.min_split}",
        f"--max-overall-download-limit={cfg.limits.global_rate}",
        f"--max-download-limit={cfg.limits.per_download}",
        f"--save-session={state / 'session'}",
        "--save-session-interval=30",
        "--force-save=true",
        f"--on-download-complete={complete}",
        f"--on-download-error={error}",
        f"--log={state / 'aria2.log'}",
        "--log-level=error",
    ]
    session = state / "session"
    if session.exists():
        args.append(f"--input-file={session}")
    return args


def _probe(port: int, secret: str) -> str:
    client = Aria2("127.0.0.1", port, secret, timeout=1.0)
    try:
        client.get_version()
        return "ours"
    except Aria2Error:
        return "foreign"
    except Aria2Unreachable:
        return "free"


def _spawn(cfg: Config, state: Path, port: int, secret: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "spawn.log", "wb") as log:
        subprocess.Popen(
            aria2_args(cfg, state, port, secret),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=os.path.expanduser("~"),
        )


def _tail_log(state: Path, lines: int = 20) -> str:
    for name in ("aria2.log", "spawn.log"):
        target = state / name
        if target.exists():
            body = target.read_text(errors="replace").splitlines()[-lines:]
            if body:
                return "\n".join(body)
    return "(no log output)"


def ensure_running(cfg: Config, state: Path = STATE_DIR) -> Aria2:
    if shutil.which("aria2c") is None:
        raise Aria2Missing("aria2c not found — brew install aria2")

    secret = read_secret(state)
    preferred = read_port(state)
    candidates = [preferred] + [p for p in PORT_RANGE if p != preferred]

    free_port = None
    for port in candidates:
        status = _probe(port, secret)
        if status == "ours":
            write_port(state, port)
            return Aria2("127.0.0.1", port, secret)
        if status == "free" and free_port is None:
            free_port = port

    if free_port is None:
        raise DaemonStartFailed(f"no free port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}")

    _spawn(cfg, state, free_port, secret)
    client = Aria2("127.0.0.1", free_port, secret, timeout=1.0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            client.get_version()
            write_port(state, free_port)
            return Aria2("127.0.0.1", free_port, secret)
        except (Aria2Unreachable, Aria2Error):
            time.sleep(0.1)

    quarantine_session(state)
    raise DaemonStartFailed(f"aria2c did not answer within 5s\n{_tail_log(state)}")
