import os
from pathlib import Path

from .daemon import alive

LOCK = "dl.lock"


def holder(state: Path) -> int:
    """The pid of the running dashboard, or 0.

    A lock left behind by a crash names a pid that is gone, and must not keep
    the dashboard shut for good.
    """
    try:
        pid = int((state / LOCK).read_text().strip())
    except (OSError, ValueError):
        return 0
    return pid if alive(pid) else 0


def acquire(state: Path, pid: int | None = None) -> bool:
    running = holder(state)
    mine = os.getpid() if pid is None else pid
    if running and running != mine:
        return False
    state.mkdir(parents=True, exist_ok=True)
    (state / LOCK).write_text(str(mine))
    return True


def release(state: Path, pid: int | None = None) -> None:
    mine = os.getpid() if pid is None else pid
    if holder(state) == mine:
        (state / LOCK).unlink(missing_ok=True)
