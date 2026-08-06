import json
import os
import secrets
import shutil
import signal
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from .youtube import Choices, build_args, burns_in

SUBTITLE_SUFFIXES = (".vtt", ".srt", ".ass")
_SKIP_SUFFIXES = (".aria2", ".ytdl", ".json", ".tmp")
OUTPUT_TEMPLATE = "%(title)s.%(ext)s"


def new_job(
    url: str, directory: Path, choices: Choices, proxy: str = "", cookies_from: str = ""
) -> dict:
    return {
        "id": f"yt-{secrets.token_hex(6)}",
        "url": url,
        "dir": str(directory),
        "choices": asdict(choices),
        "proxy": proxy,
        "cookies_from": cookies_from,
        "outname": "",
        "force": False,
        "status": "queued",
        "title": "",
        "pid": 0,
        "supervisor": 0,
        "done": 0,
        "total": 0,
        "speed": 0,
        "file": "",
        "error": "",
        "started": int(time.time()),
    }


def choices_of(job: dict) -> Choices:
    return Choices(**job["choices"])


def save(directory: Path, job: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{job['id']}.json"
    staging = target.with_suffix(".json.writing")
    staging.write_text(json.dumps(job, ensure_ascii=False))
    staging.replace(target)
    return target


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def list_jobs(directory: Path) -> list[dict]:
    if not directory.is_dir():
        return []
    jobs = []
    for path in sorted(directory.glob("yt-*.json")):
        try:
            jobs.append(read(path))
        except (OSError, json.JSONDecodeError):
            continue
    return jobs


def scratch_dir(state: Path, job: dict) -> Path:
    """Fragments live here, never in the destination: two jobs sharing a folder
    would otherwise be indistinguishable from each other's leftovers."""
    return state / f"{job['id']}.part"


def result_marker(state: Path, job: dict) -> Path:
    return state / f"{job['id']}.final"


def command(job: dict, state: Path) -> list[str]:
    """yt-dlp invocation for this job, transfers delegated to aria2c."""
    argv = ["yt-dlp", "--newline", "--no-colors", "--ignore-errors"]
    argv += ["--downloader", "aria2c"]
    argv += ["--downloader-args", "aria2c:-x16 -s16 -k1M --summary-interval=1"]
    if job.get("proxy"):
        argv += ["--proxy", job["proxy"]]
    if job.get("cookies_from"):
        # YouTube refuses anonymous requests with "confirm you're not a bot".
        argv += ["--cookies-from-browser", job["cookies_from"]]
    argv += build_args(choices_of(job))
    argv += ["-P", f"home:{job['dir']}", "-P", f"temp:{scratch_dir(state, job)}"]
    # yt-dlp knows exactly what it produced; guessing from the folder does not.
    argv += ["--print-to-file", "after_move:filepath", str(result_marker(state, job))]
    if job.get("force"):
        argv.append("--force-overwrites")
    argv += ["-o", job.get("outname") or OUTPUT_TEMPLATE]
    argv.append(job["url"])
    return argv


def probe_command(job: dict) -> list[str]:
    """Ask yt-dlp what this will be before fetching it.

    Without a total there is no percentage and no bar, and the row shows a URL
    instead of a title until the moment it finishes.
    """
    argv = ["yt-dlp", "--no-warnings", "--simulate", "--no-playlist"]
    if job.get("proxy"):
        argv += ["--proxy", job["proxy"]]
    if job.get("cookies_from"):
        argv += ["--cookies-from-browser", job["cookies_from"]]
    argv += build_args(choices_of(job))
    argv += ["-P", f"home:{job['dir']}", "-o", OUTPUT_TEMPLATE]
    # %(filename)s is yt-dlp's own sanitised path — the only reliable way to
    # know what it will write before it writes it.
    argv += [
        "--print",
        "%(title)s",
        "--print",
        "%(filename)s",
        "--print",
        "%(filesize,filesize_approx)s",
    ]
    argv.append(job["url"])
    return argv


def parse_probe(output: str) -> tuple[str, str, int]:
    """Title, destination path and total bytes from probe_command's output."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if not lines:
        return "", "", 0
    title = lines[0]
    filename = lines[1] if len(lines) > 1 and not lines[1].isdigit() else ""
    total = next((int(line) for line in lines[1:] if line.isdigit()), 0)
    return title, filename, total


def produced_file(state: Path, job: dict) -> Path | None:
    marker = result_marker(state, job)
    try:
        line = marker.read_text(encoding="utf-8").strip().splitlines()[-1]
    except (OSError, IndexError):
        return None
    landed = Path(line)
    return landed if landed.is_file() else None


def _countable(path: Path) -> bool:
    return path.is_file() and not path.name.endswith(_SKIP_SUFFIXES)


def bytes_on_disk(directory: Path) -> int:
    """Progress with an external downloader has to be read off the filesystem —
    yt-dlp stops reporting bytes once aria2c owns the transfer."""
    if not directory.is_dir():
        return 0
    return sum(p.stat().st_size for p in directory.iterdir() if _countable(p))


def clean_scratch(state: Path, job: dict) -> None:
    shutil.rmtree(scratch_dir(state, job), ignore_errors=True)
    result_marker(state, job).unlink(missing_ok=True)


def burn_command(video: Path, subtitles: Path) -> tuple[list[str], Path]:
    """Burn subtitles into the picture. ffmpeg cannot write over its own input,
    so this renders beside it and the caller swaps the result in.

    Run with cwd set to the video's folder: bare filenames keep the filtergraph
    free of the ':' and '\\' that a full path would need escaped.
    """
    out = video.with_name(f"{video.stem}.subbed{video.suffix}")
    escaped = subtitles.name.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    return (
        ["ffmpeg", "-y", "-i", video.name, "-vf", f"subtitles={escaped}", "-c:a", "copy", out.name],
        out,
    )


FFMPEG_ADVICE = "yt-dlp needs ffmpeg for this — brew install ffmpeg"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def needs_ffmpeg(choices: Choices) -> bool:
    """Whether this download has to be put together after it is fetched.

    Which is nearly always: YouTube serves video and audio as separate
    streams above 360p, and combining them is ffmpeg's job. Audio-only and
    subtitles need it too.
    """
    return choices.audio_only or choices.subs != "off" or choices.video != "none"


def burn_in_available() -> bool:
    """The subtitles filter needs an ffmpeg built with libass, which plenty of
    builds are not."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        listed = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == ["subtitles"] for line in listed.splitlines() if line.strip())


def subtitle_for(directory: Path, lang: str) -> Path | None:
    if not directory.is_dir():
        return None
    tracks = [p for p in directory.iterdir() if p.name.endswith(SUBTITLE_SUFFIXES)]
    preferred = [p for p in tracks if f".{lang}." in p.name]
    return (preferred or tracks or [None])[0]


def wants_burn_in(job: dict) -> bool:
    return burns_in(choices_of(job))


def pause(directory: Path, job: dict) -> dict:
    """Record the pause and let the supervisor act on it.

    Signalling yt-dlp from here would race the supervisor's poll loop into
    finalize(), which reads a terminated process as a failed download.
    """
    job.update(status="paused", speed=0)
    save(directory, job)
    return job


def stop(job: dict) -> None:
    """Cancel a running job. The supervisor watches for this status and takes
    yt-dlp down with it, so the process group dies with the record."""
    pid = job.get("pid", 0)
    if not running(pid):
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


UNFINISHED = ("queued", "active", "burning")
KEEP_FINISHED = 3600


def _recorded_urls(history_log: Path | None) -> set[str]:
    if history_log is None:
        return set()
    from . import history

    return {record.get("url", "") for record in history.tail(history_log, 1000)}


def orphaned(job: dict) -> bool:
    """A record still claiming to run with nothing behind it.

    Only a supervisor known to have existed counts. A job spawned moments ago
    has not written its pid yet, and the probe can hold it there for minutes.
    """
    if job.get("status") not in UNFINISHED:
        return False
    watcher = job.get("supervisor", 0)
    return bool(watcher) and not running(watcher)


def reap(directory: Path, job: dict) -> dict:
    if not orphaned(job):
        return job
    job.update(status="error", error="stopped — nothing is downloading this", speed=0)
    save(directory, job)
    return job


def sweep(
    directory: Path,
    history_log: Path | None = None,
    keep_finished: int = KEEP_FINISHED,
    now: float | None = None,
) -> None:
    """Bring the job directory back in line with reality.

    Fragments are the reason this exists: they live outside the destination
    folder, so a job that ends any way other than cleanly leaves them where
    nothing will ever look.

    A finished record is only dropped once history.jsonl carries the download,
    never on age alone. These records are the fallback when the handover did
    not happen, and dropping one that history never received would erase the
    only trace of it.
    """
    if not directory.is_dir():
        return
    moment = time.time() if now is None else now
    recorded = _recorded_urls(history_log)
    live: set[str] = set()
    for job in list_jobs(directory):
        reap(directory, job)
        record = directory / f"{job['id']}.json"
        finished = job.get("status") in ("complete", "cancelled")
        aged = moment - record.stat().st_mtime > keep_finished
        if finished and aged and job.get("url", "") in recorded:
            record.unlink(missing_ok=True)
            record.with_suffix(".log").unlink(missing_ok=True)
            continue
        live.add(job["id"])

    for scratch in directory.glob("yt-*.part"):
        if scratch.name.removesuffix(".part") not in live:
            shutil.rmtree(scratch, ignore_errors=True)
    for marker in directory.glob("yt-*.final"):
        if marker.stem not in live:
            marker.unlink(missing_ok=True)
