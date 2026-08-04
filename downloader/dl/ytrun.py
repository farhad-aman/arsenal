import subprocess
import sys
import time
from pathlib import Path

from . import history, ytjob
from .config import STATE_DIR, load
from .hook import notify

POLL = 0.5


def _update(state: Path, job: dict, **fields) -> dict:
    job.update(fields)
    ytjob.save(state, job)
    return job


def burn_in(job: dict) -> str:
    """Render the subtitle track into the picture, replacing the original."""
    directory = Path(job["dir"])
    video = ytjob.final_file(directory)
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
        problem = burn_in(job)
        if problem:
            return _update(state, job, status="error", error=problem)

    landed = ytjob.final_file(directory)
    job = _update(
        state,
        job,
        status="complete",
        file=str(landed) if landed else "",
        title=landed.stem if landed else job.get("title", ""),
        done=landed.stat().st_size if landed else job["done"],
    )
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

    log = state / f"{job['id']}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(log, "wb") as sink:
            proc = subprocess.Popen(
                ytjob.command(job),
                stdout=sink,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                cwd=str(directory),
            )
    except OSError as exc:
        _update(state, job, status="error", error=f"yt-dlp not runnable: {exc}")
        return 1

    _update(state, job, status="active", pid=proc.pid)
    last, moved_at = 0, time.monotonic()
    while proc.poll() is None:
        time.sleep(POLL)
        done = ytjob.bytes_on_disk(directory)
        now = time.monotonic()
        speed = int((done - last) / max(now - moved_at, 0.001)) if done > last else 0
        if done != last:
            last, moved_at = done, now
        current = ytjob.read(state / f"{job['id']}.json")
        if current.get("status") == "cancelled":
            proc.terminate()
            return 0
        job.update(current)
        _update(state, job, done=done, speed=max(speed, 0))

    finalize(state, job, proc.returncode, cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
