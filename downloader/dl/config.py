import os
import re
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dl"
STATE_DIR = (
    Path(os.environ["DL_STATE_DIR"])
    if os.environ.get("DL_STATE_DIR")
    else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dl"
)
CONFIG_FILE = (
    Path(os.environ["DL_CONFIG_FILE"])
    if os.environ.get("DL_CONFIG_FILE")
    else CONFIG_DIR / "config.toml"
)

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
    ascii_icons: bool
    notify: bool


@dataclass(frozen=True)
class Limits:
    per_download: str
    connections: int
    splits: int
    min_split: str


@dataclass(frozen=True)
class Config:
    general: General
    limits: Limits
    categories: dict[str, Category]
    domains: dict[str, str]


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
    ascii_icons=False,
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
    )


def _general_from(raw: dict) -> General:
    g = _DEFAULT_GENERAL
    return replace(
        g,
        default_dir=_expand(raw["default_dir"]) if "default_dir" in raw else g.default_dir,
        max_concurrent=int(raw["max_concurrent"]) if "max_concurrent" in raw else g.max_concurrent,
        idle_timeout=parse_duration(raw["idle_timeout"]) if "idle_timeout" in raw else g.idle_timeout,
        theme=str(raw["theme"]) if "theme" in raw else g.theme,
        ascii_icons=bool(raw["ascii_icons"]) if "ascii_icons" in raw else g.ascii_icons,
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
ascii_icons     = false
notify          = true

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
