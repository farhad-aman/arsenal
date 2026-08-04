import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict
from pathlib import Path

from .youtube import Choices, build_args, burns_in

SUBTITLE_SUFFIXES = (".vtt", ".srt", ".ass")
_SKIP_SUFFIXES = (".aria2", ".ytdl", ".json", ".tmp")
OUTPUT_TEMPLATE = "%(title)s.%(ext)s"


def new_job(url: str, directory: Path, choices: Choices, proxy: str = "") -> dict:
    return {
        "id": f"yt-{secrets.token_hex(6)}",
        "url": url,
        "dir": str(directory),
        "choices": asdict(choices),
        "proxy": proxy,
        "status": "queued",
        "title": "",
        "pid": 0,
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


def command(job: dict) -> list[str]:
    """yt-dlp invocation for this job, transfers delegated to aria2c."""
    argv = ["yt-dlp", "--newline", "--no-colors", "--ignore-errors"]
    argv += ["--downloader", "aria2c"]
    argv += ["--downloader-args", "aria2c:-x16 -s16 -k1M --summary-interval=1"]
    if job.get("proxy"):
        argv += ["--proxy", job["proxy"]]
    argv += build_args(choices_of(job))
    argv += ["-o", str(Path(job["dir"]) / OUTPUT_TEMPLATE)]
    argv.append(job["url"])
    return argv


def _countable(path: Path) -> bool:
    return path.is_file() and not path.name.endswith(_SKIP_SUFFIXES)


def bytes_on_disk(directory: Path) -> int:
    """Progress with an external downloader has to be read off the filesystem —
    yt-dlp stops reporting bytes once aria2c owns the transfer."""
    if not directory.is_dir():
        return 0
    return sum(p.stat().st_size for p in directory.iterdir() if _countable(p))


def final_file(directory: Path) -> Path | None:
    if not directory.is_dir():
        return None
    media = [
        p
        for p in directory.iterdir()
        if _countable(p)
        and not p.name.endswith(SUBTITLE_SUFFIXES)
        and not p.name.endswith(".part")
    ]
    return max(media, key=lambda p: p.stat().st_size, default=None)


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


def running(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
