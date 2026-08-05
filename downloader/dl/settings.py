import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, parse_duration
from .destinations import ensure_writable
from .theme import THEMES

_RATE = re.compile(r"^(\d+[KMG]?|off)$", re.IGNORECASE)
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

# Settings whose own screen owns them, rather than a Field.
LIST_SECTIONS = frozenset({"categories", "domains", "headers", "proxy", "proxy_domains"})

# Config attribute names the screen can reach. Named separately from the schema
# because a Field's path is its TOML key, and the two differ: hooks.timeout is
# Config.hook_timeout. Explicit so adding a setting forces a decision rather
# than defaulting to invisible.
EDITABLE = LIST_SECTIONS | {
    "theme",
    "ascii_icons",
    "notify",
    "default_dir",
    "idle_timeout",
    "max_concurrent",
    "connections",
    "splits",
    "min_split",
    "per_download",
    "cookies_from",
    "probe_timeout",
    "on_complete",
    "hook_timeout",
}


class Invalid(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]
    label: str
    kind: str
    choices: tuple[str, ...] = ()
    help: str = ""
    live: bool = False
    allow_empty: bool = False


GENERAL = (
    Field(("general", "theme"), "Theme", "choice", tuple(THEMES), live=True),
    Field(("general", "ascii_icons"), "ASCII icons", "bool", live=True),
    Field(("general", "notify"), "Notifications", "bool"),
    Field(("general", "default_dir"), "Default folder", "path"),
    Field(("general", "idle_timeout"), "Daemon idle timeout", "duration"),
)

LIMITS = (
    Field(("general", "max_concurrent"), "Parallel downloads", "int"),
    Field(("limits", "connections"), "Connections per server", "int"),
    Field(("limits", "splits"), "Segments per file", "int"),
    Field(("limits", "min_split"), "Smallest segment", "rate"),
    Field(("limits", "per_download"), "Speed cap per download", "rate"),
)

YOUTUBE = (
    Field(("youtube", "cookies_from"), "Cookies from browser", "text", allow_empty=True),
    Field(("youtube", "probe_timeout"), "Probe timeout", "duration"),
)

HOOKS = (
    Field(("hooks", "on_complete"), "Run after each download", "text", allow_empty=True),
    Field(("hooks", "timeout"), "Hook timeout", "duration"),
)

CATEGORY_FIELDS = (
    Field(("dir",), "Folder", "path"),
    Field(("ext",), "Extensions", "text"),
    Field(("icon",), "Icon", "text"),
    Field(("hue",), "Colour", "colour"),
)

ATTRIBUTE = {
    ("general", "max_concurrent"): lambda cfg: cfg.general.max_concurrent,
    ("general", "theme"): lambda cfg: cfg.general.theme,
    ("general", "ascii_icons"): lambda cfg: cfg.general.ascii_icons,
    ("general", "notify"): lambda cfg: cfg.general.notify,
    ("general", "default_dir"): lambda cfg: str(cfg.general.default_dir),
    ("general", "idle_timeout"): lambda cfg: f"{cfg.general.idle_timeout}s",
    ("limits", "connections"): lambda cfg: cfg.limits.connections,
    ("limits", "splits"): lambda cfg: cfg.limits.splits,
    ("limits", "min_split"): lambda cfg: cfg.limits.min_split,
    ("limits", "per_download"): lambda cfg: cfg.limits.per_download or "off",
    ("youtube", "cookies_from"): lambda cfg: cfg.cookies_from,
    ("youtube", "probe_timeout"): lambda cfg: f"{cfg.probe_timeout}s",
    ("hooks", "on_complete"): lambda cfg: cfg.on_complete,
    ("hooks", "timeout"): lambda cfg: f"{cfg.hook_timeout}s",
}


def current(cfg: Config, field: Field):
    return ATTRIBUTE[field.path](cfg)


def render(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def parse(field: Field, raw: str):
    text = raw.strip()
    if field.kind == "choice":
        if text not in field.choices:
            raise Invalid(f"pick one of: {', '.join(field.choices)}")
        return text
    if field.kind == "bool":
        if text not in ("on", "off"):
            raise Invalid("on or off")
        return text == "on"
    if field.kind == "int":
        if not text.isdigit() or int(text) < 1:
            raise Invalid("a whole number, 1 or more")
        return int(text)
    if field.kind == "rate":
        if not _RATE.match(text):
            raise Invalid("a size like 500K, 2M, 1G — or off")
        return text
    if field.kind == "duration":
        try:
            parse_duration(text)
        except ValueError:
            raise Invalid("a duration like 30s, 10m, 2h") from None
        return text
    if field.kind == "colour":
        if not _COLOUR.match(text):
            raise Invalid("a hex colour like #c678dd")
        return text
    if field.kind == "path":
        where = Path(text).expanduser()
        if not ensure_writable(where):
            raise Invalid(f"cannot write to {where}")
        return str(where)
    # "" is a real setting for two fields: no completion hook, and no browser
    # to borrow cookies from.
    if not text and not field.allow_empty:
        raise Invalid("cannot be empty")
    return text
