import os
import re
import sys
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

# Fixed, with nothing in the environment able to move them. dl is one app: one
# daemon, one dashboard, one queue, one history. An environment variable that
# relocated any of these would be a way to run a second copy, and two copies
# acting on downloads neither can see is the inconsistency this prevents.
# Isolating a whole run belongs to a container, not to dl.
CONFIG_DIR = Path.home() / ".config" / "dl"
STATE_DIR = Path.home() / ".local" / "state" / "dl"
CONFIG_FILE = CONFIG_DIR / "config.toml"

_DURATION = re.compile(r"^(\d+)\s*([smh]?)$")
_MULTIPLIER = {"": 1, "s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class Category:
    name: str
    dir: Path
    ext: tuple[str, ...]
    icon: str
    hue: str


@dataclass(frozen=True)
class General:
    default_dir: Path
    max_concurrent: int
    idle_timeout: int
    theme: str
    notify: bool


@dataclass(frozen=True)
class Limits:
    per_download: str
    connections: int
    splits: int
    min_split: str


DEFAULT_PROXY = "http://127.0.0.1:2080"
DEFAULT_COOKIES = "chrome"
DEFAULT_PROBE_TIMEOUT = 180
DEFAULT_NEWEST = 100
DEFAULT_HOOK_TIMEOUT = 300


@dataclass(frozen=True)
class Config:
    general: General
    limits: Limits
    categories: dict[str, Category]
    domains: dict[str, str]
    proxy: str = DEFAULT_PROXY
    proxy_domains: tuple[str, ...] = ()
    cookies_from: str = DEFAULT_COOKIES
    probe_timeout: int = DEFAULT_PROBE_TIMEOUT
    newest: int = DEFAULT_NEWEST
    headers: dict[str, dict[str, str]] = field(default_factory=dict)
    on_complete: str = ""
    hook_timeout: int = DEFAULT_HOOK_TIMEOUT


def _positive(value, fallback: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number > 0 else fallback


def parse_duration(text: str) -> int:
    m = _DURATION.match(str(text).strip().lower())
    if not m:
        raise ValueError(f"bad duration: {text!r}")
    return int(m.group(1)) * _MULTIPLIER[m.group(2)]


def parse_rate(text: str) -> str:
    value = str(text).strip()
    if not value or value.lower() == "off":
        return "0"
    return value


def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _cat(name: str, directory: str, ext: list[str], icon: str, hue: str) -> Category:
    return Category(
        name=name,
        dir=_expand(directory),
        ext=tuple(e.lower().lstrip(".") for e in ext),
        icon=icon,
        hue=hue,
    )


DEFAULT_CATEGORIES: dict[str, Category] = {
    "video": _cat("video", "~/Movies/Downloads", ["mkv", "mp4", "avi", "mov", "webm", "m4v"], "🎬", "#c678dd"),
    "iso": _cat("iso", "~/Downloads/ISO", ["iso", "img", "dmg", "vhd"], "💿", "#4aa3ff"),
    "archive": _cat("archive", "~/Downloads/Archives", ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz"], "📦", "#e5a44b"),
    "audio": _cat("audio", "~/Music/Downloads", ["mp3", "flac", "wav", "m4a", "ogg", "opus"], "🎵", "#f07eb0"),
    "docs": _cat("docs", "~/Documents/Downloads", ["pdf", "epub", "docx", "xlsx", "pptx", "txt", "md"], "📄", "#4ecdc4"),
    "apps": _cat("apps", "~/Downloads/Apps", ["pkg", "app", "deb", "rpm", "exe", "msi"], "⚙️", "#5ac26a"),
    "models": _cat("models", "~/Downloads/Models", ["safetensors", "gguf", "ckpt", "pt", "pth", "bin"], "🧠", "#9b7bea"),
    "code": _cat("code", "~/Downloads/Code", ["patch", "diff", "whl", "jar"], "💻", "#e58a3c"),
}

DEFAULT_DOMAINS: dict[str, str] = {"huggingface.co": "models", "*.github.com": "code"}

_DEFAULT_GENERAL = General(
    default_dir=_expand("~/Downloads"),
    max_concurrent=3,
    idle_timeout=600,
    theme="aurora",
    notify=True,
)

_DEFAULT_LIMITS = Limits(
    per_download="0", connections=16, splits=16, min_split="1M"
)


def defaults() -> Config:
    return Config(
        general=_DEFAULT_GENERAL,
        limits=_DEFAULT_LIMITS,
        categories=dict(DEFAULT_CATEGORIES),
        domains=dict(DEFAULT_DOMAINS),
        proxy=DEFAULT_PROXY,
    )


def _general_from(raw: dict) -> General:
    g = _DEFAULT_GENERAL
    return replace(
        g,
        default_dir=_expand(raw["default_dir"]) if "default_dir" in raw else g.default_dir,
        max_concurrent=int(raw["max_concurrent"]) if "max_concurrent" in raw else g.max_concurrent,
        idle_timeout=parse_duration(raw["idle_timeout"]) if "idle_timeout" in raw else g.idle_timeout,
        theme=str(raw["theme"]) if "theme" in raw else g.theme,
        notify=bool(raw["notify"]) if "notify" in raw else g.notify,
    )


def _limits_from(raw: dict) -> Limits:
    lim = _DEFAULT_LIMITS
    return replace(
        lim,
        per_download=parse_rate(raw["per_download"]) if "per_download" in raw else lim.per_download,
        connections=int(raw["connections"]) if "connections" in raw else lim.connections,
        splits=int(raw["splits"]) if "splits" in raw else lim.splits,
        min_split=str(raw["min_split"]) if "min_split" in raw else lim.min_split,
    )


def _categories_from(raw: dict) -> dict[str, Category]:
    cats = dict(DEFAULT_CATEGORIES)
    for name, body in raw.items():
        base = cats.get(name)
        cats[name] = _cat(
            name,
            body.get("dir", str(base.dir) if base else "~/Downloads"),
            list(body.get("ext", list(base.ext) if base else [])),
            body.get("icon", base.icon if base else "📥"),
            body.get("hue", base.hue if base else "#888888"),
        )
    return cats


def load(path: Path | None = None) -> Config:
    target = path or CONFIG_FILE
    if not target.exists():
        return defaults()
    try:
        with open(target, "rb") as fh:
            raw = tomllib.load(fh)
        return Config(
            general=_general_from(raw.get("general", {})),
            limits=_limits_from(raw.get("limits", {})),
            categories=_categories_from(raw.get("categories", {})),
            domains={str(k).lower(): str(v) for k, v in raw.get("domains", DEFAULT_DOMAINS).items()},
            proxy=str(raw.get("proxy", {}).get("url", DEFAULT_PROXY)),
            proxy_domains=tuple(
                str(d).lower() for d in raw.get("proxy", {}).get("domains", [])
            ),
            cookies_from=str(raw.get("youtube", {}).get("cookies_from", DEFAULT_COOKIES)),
            newest=_positive(raw.get("youtube", {}).get("newest"), DEFAULT_NEWEST),
            probe_timeout=(
                parse_duration(raw["youtube"]["probe_timeout"])
                if "probe_timeout" in raw.get("youtube", {})
                else DEFAULT_PROBE_TIMEOUT
            ),
            headers={
                str(host).lower(): {str(k): str(v) for k, v in fields.items()}
                for host, fields in raw.get("headers", {}).items()
            },
            on_complete=str(raw.get("hooks", {}).get("on_complete", "")),
            hook_timeout=(
                parse_duration(raw["hooks"]["timeout"])
                if "timeout" in raw.get("hooks", {})
                else DEFAULT_HOOK_TIMEOUT
            ),
        )
    except (tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f"dl: {target} is invalid ({exc}) — using defaults", file=sys.stderr)
        return defaults()


DEFAULT_TOML = """\
[general]
default_dir     = "~/Downloads"
max_concurrent  = 3
idle_timeout    = "10m"
theme           = "aurora"
notify          = true

[proxy]
url = "http://127.0.0.1:2080"
# Hosts always downloaded through it, so -p is only needed for one-offs. A bare
# name covers its subdomains too — a blocked service is blocked at every
# hostname it answers on. "*." matches subdomains only.
domains = []
# domains = ["youtube.com", "googlevideo.com"]

[youtube]
# YouTube refuses anonymous requests ("confirm you're not a bot"), so yt-dlp
# borrows cookies from this browser. Set to "" to send none.
cookies_from = "chrome"
# How long to wait for YouTube to say what a link is before downloading it.
# Over a proxy this ranges from seconds to minutes; giving up early costs the
# title, the size and the check for a copy already on disk.
probe_timeout = "3m"
# How many a playlist or channel takes when you choose "newest only" rather
# than all of it.
newest = 100

# Sent with every request to a matching host. Same host rule as [proxy.domains]:
# a bare name covers subdomains, "*." matches subdomains only. Useful for hosts
# that check Referer. Anything secret here is only as private as this file.
# [headers."indllserver.info"]
# Referer = "https://indllserver.info/"

[hooks]
# Run after every finished download, as: <command> <path> <category> <url>
# Runs for YouTube downloads too. A hook that fails never fails the download.
on_complete = ""
timeout     = "5m"

[limits]
per_download = "off"
connections  = 16
splits       = 16
min_split    = "1M"

[categories.video]
dir  = "~/Movies/Downloads"
ext  = ["mkv", "mp4", "avi", "mov", "webm", "m4v"]
icon = "🎬"
hue  = "#c678dd"

[categories.iso]
dir  = "~/Downloads/ISO"
ext  = ["iso", "img", "dmg", "vhd"]
icon = "💿"
hue  = "#4aa3ff"

[categories.archive]
dir  = "~/Downloads/Archives"
ext  = ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz"]
icon = "📦"
hue  = "#e5a44b"

[categories.audio]
dir  = "~/Music/Downloads"
ext  = ["mp3", "flac", "wav", "m4a", "ogg", "opus"]
icon = "🎵"
hue  = "#f07eb0"

[categories.docs]
dir  = "~/Documents/Downloads"
ext  = ["pdf", "epub", "docx", "xlsx", "pptx", "txt", "md"]
icon = "📄"
hue  = "#4ecdc4"

[categories.apps]
dir  = "~/Downloads/Apps"
ext  = ["pkg", "app", "deb", "rpm", "exe", "msi"]
icon = "⚙️"
hue  = "#5ac26a"

[categories.models]
dir  = "~/Downloads/Models"
ext  = ["safetensors", "gguf", "ckpt", "pt", "pth", "bin"]
icon = "🧠"
hue  = "#9b7bea"

[categories.code]
dir  = "~/Downloads/Code"
ext  = ["patch", "diff", "whl", "jar"]
icon = "💻"
hue  = "#e58a3c"

[domains]
"huggingface.co" = "models"
"*.github.com"   = "code"
"""


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML)
