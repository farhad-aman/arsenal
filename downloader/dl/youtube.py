from dataclasses import dataclass
from urllib.parse import urlparse

_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
        "www.youtu.be",
    }
)

VIDEO_CHOICES = ("best", "2160", "1440", "1080", "720", "480", "360", "none")
AUDIO_CHOICES = ("best", "256", "192", "128", "96")
SUB_CHOICES = ("off", "soft", "hard")
VIDEO_CONTAINERS = ("mp4", "mkv", "webm")
AUDIO_CONTAINERS = ("m4a", "mp3", "opus", "flac")


@dataclass(frozen=True)
class Choices:
    video: str
    audio: str
    subs: str
    sub_lang: str
    container: str

    @property
    def audio_only(self) -> bool:
        return self.video == "none"


DEFAULTS = Choices(video="best", audio="best", subs="off", sub_lang="en", container="mp4")


def is_youtube(url: str) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return host in _HOSTS


def burns_in(choices: Choices) -> bool:
    """Hard subtitles need an ffmpeg pass after yt-dlp is done."""
    return choices.subs == "hard"


def _selector(choices: Choices) -> str:
    if choices.audio_only:
        return "ba/b"
    if choices.video == "best":
        return "bv*+ba/b"
    return f"bv*[height<={choices.video}]+ba/b[height<={choices.video}]"


def build_args(choices: Choices) -> list[str]:
    """yt-dlp flags for these choices, without the URL or the destination."""
    args = ["-f", _selector(choices)]

    if choices.audio_only:
        args += ["-x", "--audio-format", choices.container]
        if choices.audio != "best":
            args += ["--audio-quality", f"{choices.audio}K"]
    else:
        args += ["--merge-output-format", choices.container]

    if choices.subs != "off":
        args += ["--write-subs", "--write-auto-subs", "--sub-langs", choices.sub_lang]
        if choices.subs == "soft":
            args.append("--embed-subs")
    return args
