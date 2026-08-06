import os
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from . import config as config_module
from .config import STATE_DIR, Config
from .rpc import Aria2, Aria2Error, Aria2Unreachable

# One port, fixed. Roaming to the next free port when this one was busy is how
# a second daemon came to exist beside the first, holding downloads nothing
# could reach and nothing knew about.
PORT = 6810

# The paths travel as arguments rather than in the environment: nothing in the
# environment can move dl's state, so there is nothing there for the shim to
# set. A test daemon's hook still writes to the test's own directory because
# that is what the daemon was told to use.
_SHIM = (
    "#!/bin/sh\n"
    'exec {python} -m dl.hook {mode} --state {state} --config {config} "$@"\n'
)


_PROXY_ENV = ("http_proxy", "https_proxy", "ftp_proxy", "all_proxy", "no_proxy")


def spawn_env() -> dict:
    """aria2 reads http_proxy and friends from the environment. The daemon
    outlives the shell that started it, so inheriting them would let a stale
    `vpn -p` proxy every later download. -p is the only proxy switch."""
    return {k: v for k, v in os.environ.items() if k.lower() not in _PROXY_ENV}


def _config_path() -> Path:
    """Read at call time, not import time, so the shim carries the config the
    parent is actually using."""
    return Path(config_module.CONFIG_FILE)


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
        return PORT


def read_pid(state: Path) -> int:
    try:
        return int((state / "daemon.pid").read_text().strip())
    except (OSError, ValueError):
        return 0


def write_pid(state: Path, pid: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "daemon.pid").write_text(str(pid))


def clear_pid(state: Path) -> None:
    (state / "daemon.pid").unlink(missing_ok=True)


def alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate(pid: int, wait: float = 5.0) -> None:
    """Stop a daemon of ours that no longer answers its own secret."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def write_port(state: Path, port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "port").write_text(str(port))


def write_hook_shims(state: Path, python: str) -> tuple[Path, Path]:
    hooks = state / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    written = []
    for mode in ("complete", "error"):
        target = hooks / f"{mode}.sh"
        target.write_text(
            _SHIM.format(python=python, mode=mode, state=state, config=_config_path())
        )
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
        f"--max-download-limit={cfg.limits.per_download}",
        f"--save-session={state / 'session'}",
        "--save-session-interval=30",
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


def _bindable(port: int) -> bool:
    """A daemon shutting down refuses connections while still holding its
    listening socket, so 'connection refused' does not imply we can bind."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_bindable(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        if _bindable(port):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)


def _spawn(cfg: Config, state: Path, port: int, secret: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "spawn.log", "wb") as log:
        process = subprocess.Popen(
            aria2_args(cfg, state, port, secret),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=os.path.expanduser("~"),
            env=spawn_env(),
        )
    write_pid(state, process.pid)


_LISTEN_PORT = re.compile(r"--rpc-listen-port=(\d+)")


def aria2_processes() -> list[tuple[int, int]]:
    """Every aria2 on the machine, as (pid, port).

    Found by process rather than by scanning a range of ports: a range only
    finds what happens to be inside it, and the whole problem is daemons that
    ended up somewhere nobody recorded.
    """
    try:
        out = subprocess.run(
            ["ps", "-axww", "-o", "pid=,command="],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        if "aria2c" not in line:
            continue
        head, _, rest = line.strip().partition(" ")
        port = _LISTEN_PORT.search(rest)
        if not port:
            continue
        try:
            found.append((int(head), int(port.group(1))))
        except ValueError:
            continue
    return found


def strays(state: Path) -> list[tuple[int, int]]:
    """aria2 daemons this dl cannot talk to, as (pid, port).

    Older versions moved to the next free port when this one was busy, so a
    machine can be carrying daemons no state directory knows about.
    """
    secret = read_secret(state)
    # Anything answering our secret is ours wherever it sits, including a
    # daemon an older version left on a port it wandered to. Those are retired
    # on the next start; a stray is one nothing can talk to.
    return [
        (pid, port)
        for pid, port in aria2_processes()
        if _probe(port, secret) != "ours"
    ]


def stop_strays(found: list[tuple[int, int]]) -> int:
    stopped = 0
    for pid, _port in found:
        _terminate(pid)
        stopped += 1
    return stopped


def _tail_log(state: Path, lines: int = 20) -> str:
    for name in ("aria2.log", "spawn.log"):
        target = state / name
        if target.exists():
            body = target.read_text(errors="replace").splitlines()[-lines:]
            if body:
                return "\n".join(body)
    return "(no log output)"


def _retire_wanderer(state: Path, secret: str) -> None:
    """Bring home a daemon an older version left on some other port.

    It answers our secret, so it is ours and can be asked to stop rather than
    killed: aria2 writes its session on the way down, and the downloads it was
    carrying come back when the daemon restarts on the one port.
    """
    previous = read_port(state)
    if previous == PORT or _probe(previous, secret) != "ours":
        return
    try:
        Aria2("127.0.0.1", previous, secret, timeout=2.0).shutdown()
    except (Aria2Error, Aria2Unreachable):
        return
    # Wait for it to be gone, not merely unresponsive: aria2 writes its
    # session on the way out, and a replacement that starts first reads a
    # session file that does not yet hold the queue.
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline and _probe(previous, secret) == "ours":
        time.sleep(0.05)
    _wait_bindable(previous, 5.0)


def ensure_running(cfg: Config, state: Path = STATE_DIR) -> Aria2:
    if shutil.which("aria2c") is None:
        raise Aria2Missing("aria2c not found — brew install aria2")

    secret = read_secret(state)

    if _probe(PORT, secret) == "ours":
        write_port(state, PORT)
        return Aria2("127.0.0.1", PORT, secret)

    _retire_wanderer(state, secret)

    # Not answering, but our own pid file says it is alive: a daemon of ours
    # holding a secret it no longer shares. Nothing can reach it, so it goes
    # rather than being left to hold the port and its downloads unseen.
    stale = read_pid(state)
    if alive(stale):
        _terminate(stale)
        clear_pid(state)

    # A daemon just asked to stop keeps its listening socket for a moment, so
    # "cannot bind" right now does not mean the port belongs to someone else.
    if not _wait_bindable(PORT, 5.0):
        raise DaemonStartFailed(
            f"port {PORT} is held by something that is not dl — "
            f"stop it, or run `dl kill --strays` to see what is listening"
        )

    _spawn(cfg, state, PORT, secret)
    if _await_rpc(PORT, secret, 5.0):
        write_port(state, PORT)
        return Aria2("127.0.0.1", PORT, secret)

    quarantine_session(state)
    raise DaemonStartFailed(f"aria2c did not answer within 5s\n{_tail_log(state)}")


def _await_rpc(port: int, secret: str, timeout: float) -> bool:
    client = Aria2("127.0.0.1", port, secret, timeout=1.0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            client.get_version()
            return True
        except (Aria2Unreachable, Aria2Error):
            time.sleep(0.1)
    return False
