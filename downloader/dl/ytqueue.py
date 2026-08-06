import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from . import ytjob

CLAIM = ".claim"
LOCK = "scheduler.lock"
STALE_LOCK = 10.0

# A claim is held for as long as it keeps being touched. The supervisor
# touches its own while it works, so a slot is proof of activity rather than
# of a pid: a supervisor killed outright is never reaped by the dashboard that
# spawned it, and a zombie answers kill(pid, 0) exactly as a live one does.
HANDOVER = 30.0


def claims(directory: Path) -> list[str]:
    """Job ids with a supervisor running, or about to be.

    Counted from files rather than job records: a supervisor that has just
    been spawned has not written its pid yet, and would otherwise look idle
    for long enough to be started twice.

    Releasing happens on the way out, which a kill -9, a crash or a reboot
    never reaches — and a slot held by nothing at all is one fewer download
    for good. So a claim counts only while it is being kept fresh.
    """
    held = []
    try:
        files = sorted(directory.glob(f"*{CLAIM}"))
    except OSError:
        return []
    for path in files:
        if _fresh(path):
            held.append(path.stem)
        else:
            path.unlink(missing_ok=True)
    return held


def _fresh(path: Path) -> bool:
    try:
        return time.time() - path.stat().st_mtime < HANDOVER
    except OSError:
        return False


def touch(directory: Path, job_id: str) -> None:
    """Say the supervisor is still here."""
    path = directory / f"{job_id}{CLAIM}"
    try:
        os.utime(path, None)
    except OSError:
        hold_slot(directory, job_id)


def heartbeat(directory: Path, job_id: str):
    """Keep the claim fresh for as long as this process lives.

    A thread rather than a call in the download loop: a supervisor spends its
    first minutes asking YouTube what it is about to fetch, and a slot given
    up during the probe would be handed to something else while this one is
    still coming.

    Returns the stop signal; setting it ends the thread.
    """
    stop = threading.Event()

    def beat():
        while not stop.wait(HANDOVER / 3):
            touch(directory, job_id)

    threading.Thread(target=beat, daemon=True).start()
    return stop


def running(directory: Path) -> int:
    return len(claims(directory))


def release(directory: Path, job_id: str) -> None:
    (directory / f"{job_id}{CLAIM}").unlink(missing_ok=True)


def _lock(directory: Path):
    """Serialise the decision to start something.

    Two supervisors finishing together would otherwise both see a free slot
    and both take it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / LOCK
    for _ in range(100):
        try:
            handle = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(handle)
            return target
        except FileExistsError:
            try:
                if time.time() - target.stat().st_mtime > STALE_LOCK:
                    target.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.02)
        except OSError:
            return None
    return None


def take_slot(directory: Path, job_id: str, cap: int) -> bool:
    """Claim one of the running slots, if there is a free one."""
    lock = _lock(directory)
    try:
        if running(directory) >= max(cap, 1):
            return False
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{job_id}{CLAIM}").write_text(str(os.getpid()))
        return True
    finally:
        if lock is not None:
            lock.unlink(missing_ok=True)


def hold_slot(directory: Path, job_id: str) -> None:
    """Take a slot without asking whether there is one.

    A retry or a resume is a direct instruction about one download, not a
    request to join the back of a queue.
    """
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{job_id}{CLAIM}").write_text(str(os.getpid()))


def spawn(job: dict, state: Path) -> None:
    """Detach the supervisor so closing the shell never stops the download."""
    target = ytjob.save(state / "yt", job)
    subprocess.Popen(
        [sys.executable, "-m", "dl.ytrun", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def launch(job: dict, state: Path, cap: int) -> bool:
    """Start this job, or leave it queued for a slot to free.

    Returns whether it started. Either way the record is on disk, so the
    dashboard shows it and something can pick it up later.
    """
    directory = state / "yt"
    ytjob.save(directory, job)
    if not take_slot(directory, job["id"], cap):
        return False
    spawn(job, state)
    return True


def waiting(directory: Path) -> list[dict]:
    """Queued jobs with nothing running them, oldest first."""
    held = set(claims(directory))
    return sorted(
        (
            job
            for job in ytjob.list_jobs(directory)
            if job.get("status") == "queued" and job.get("id") not in held
        ),
        key=lambda job: job.get("started", 0),
    )


def start_next(state: Path, cap: int) -> dict | None:
    """Hand the slot on. Called by a supervisor as it leaves.

    No scheduler process and no waiting ones: whoever finishes starts the next
    thing, so a playlist queued from the command line keeps going after the
    terminal that started it has closed.
    """
    directory = state / "yt"
    for job in waiting(directory):
        if take_slot(directory, job["id"], cap):
            spawn(job, state)
            return job
    return None
