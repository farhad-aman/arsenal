import re
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

from . import history, ytjob
from .config import STATE_DIR, load
from .hook import notify

POLL = 0.5
WINDOW = 3.0
PROBE_TIMEOUT = 60


def rate(samples: deque, done: int, now: float | None = None) -> int:
    """Bytes per second over the last few seconds.

    Measuring one poll against the last reads zero whenever a poll lands
    between aria2's flushes to disk, which is most of them.
    """
    moment = time.monotonic() if now is None else now
    samples.append((moment, done))
    while len(samples) > 1 and moment - samples[0][0] > WINDOW:
        samples.popleft()
    if len(samples) < 2:
        return 0
    span = moment - samples[0][0]
    if span <= 0:
        return 0
    return max(int((done - samples[0][1]) / span), 0)


_UNITS = {"B": 1, "K": 1024, "M": 1024**2, "G": 1024**3}
_DL = re.compile(r"DL:\s*([0-9.]+)\s*([BKMG])i?B?", re.IGNORECASE)


def reported_speed(log: Path, tail: int = 4000) -> int:
    """aria2's own rate, from the summary lines it prints into the log.

    Deriving it from file growth reads as bursts, because aria2 flushes to disk
    in chunks rather than continuously.
    """
    try:
        with open(log, "rb") as fh:
            fh.seek(0, 2)
            fh.seek(max(fh.tell() - tail, 0))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return -1
    found = _DL.findall(text)
    if not found:
        return -1
    amount, unit = found[-1]
    try:
        return int(float(amount) * _UNITS[unit.upper()])
    except (ValueError, KeyError):
        return -1


def probe(job: dict) -> tuple[str, str, int]:
    try:
        done = subprocess.run(
            ytjob.probe_command(job),
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "", "", 0
    return ytjob.parse_probe(done.stdout)


def _update(state: Path, job: dict, **fields) -> dict:
    job.update(fields)
    ytjob.save(state, job)
    return job


def burn_in(state: Path, job: dict) -> str:
    """Render the subtitle track into the picture, replacing the original."""
    directory = Path(job["dir"])
    video = ytjob.produced_file(state, job)
    subtitles = ytjob.subtitle_for(directory, ytjob.choices_of(job).sub_lang)
    if video is None or subtitles is None:
        return "no subtitle track to burn in"
    if not ytjob.burn_in_available():
        return "this ffmpeg has no subtitles filter (built without libass)"
    argv, out = ytjob.burn_command(video, subtitles)
    done = subprocess.run(
        argv, capture_output=True, text=True, check=False, cwd=str(directory)
    )
    if done.returncode != 0 or not out.exists():
        return (done.stderr or "ffmpeg failed").strip().splitlines()[-1][:200]
    out.replace(video)
    subtitles.unlink(missing_ok=True)
    return ""


def last_error(log: Path) -> str:
    """yt-dlp explains itself on stderr; without this the row just says
    'exited 1' and the actual cause is lost."""
    try:
        lines = log.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        if line.startswith(("ERROR:", "WARNING:")):
            return line.split(":", 1)[1].strip()[:300]
    return lines[-1][:300] if lines else ""


def finalize(state: Path, job: dict, code: int, cfg) -> dict:
    directory = Path(job["dir"])
    if code != 0:
        detail = last_error(state / f"{job['id']}.log")
        return _update(
            state, job, status="error", error=detail or f"yt-dlp exited {code}"
        )

    if ytjob.wants_burn_in(job):
        _update(state, job, status="burning")
        problem = burn_in(state, job)
        if problem:
            return _update(state, job, status="error", error=problem)

    landed = ytjob.produced_file(state, job)
    job = _update(
        state,
        job,
        status="complete",
        file=str(landed) if landed else "",
        title=landed.stem if landed else job.get("title", ""),
        done=landed.stat().st_size if landed else job["done"],
    )
    ytjob.clean_scratch(state, job)
    history.append(
        {
            "ts": int(time.time()),
            "name": landed.name if landed else job["url"],
            "bytes": job["done"],
            "seconds": max(int(time.time()) - job["started"], 0),
            "avg_bps": 0,
            "path": job["file"],
            "category": "video",
            "url": job["url"],
            "status": "ok",
        },
        STATE_DIR / "history.jsonl",
    )
    if cfg.general.notify:
        notify("Download complete", landed.name if landed else job["url"])
    return job


def main(argv: list[str]) -> int:
    if not argv:
        return 2
    state = STATE_DIR / "yt"
    job = ytjob.read(Path(argv[0]))
    cfg = load()
    directory = Path(job["dir"])
    directory.mkdir(parents=True, exist_ok=True)

    title, _filename, total = probe(job)
    if title or total:
        _update(state, job, title=title or job.get("title", ""), total=total)

    log = state / f"{job['id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "wb") as sink:
            proc = subprocess.Popen(
                ytjob.command(job, state),
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(directory),
            )
    except OSError as exc:
        _update(state, job, status="error", error=f"yt-dlp not runnable: {exc}")
        return 1

    _update(state, job, status="active", pid=proc.pid)
    scratch = ytjob.scratch_dir(state, job)
    samples: deque[tuple[float, int]] = deque()
    while proc.poll() is None:
        time.sleep(POLL)
        done = ytjob.bytes_on_disk(scratch)
        current = ytjob.read(state / f"{job['id']}.json")
        if current.get("status") == "cancelled":
            proc.terminate()
            return 0
        job.update(current)
        measured = reported_speed(log)
        _update(
            state,
            job,
            done=done,
            speed=measured if measured >= 0 else rate(samples, done),
        )

    finalize(state, job, proc.returncode, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
