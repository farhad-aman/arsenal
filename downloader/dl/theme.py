import os
from dataclasses import dataclass

from .config import Category, Config


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    danger: str
    ok: str
    warn: str
    dim: str
    ramp: tuple[str, ...]
    mono: bool
    icons: bool


THEMES: dict[str, Theme] = {
    "aurora": Theme(
        name="aurora",
        accent="#4ecdc4",
        danger="#ff5f56",
        ok="#5ac26a",
        warn="#e5a44b",
        dim="#6b7280",
        ramp=("#1f6feb", "#4ecdc4", "#5ac26a", "#e5c44b"),
        mono=False,
        icons=True,
    ),
    "ember": Theme(
        name="ember",
        accent="#e58a3c",
        danger="#ff5f56",
        ok="#e5c44b",
        warn="#e5a44b",
        dim="#7a6a5f",
        ramp=("#7a2c1d", "#e58a3c", "#e5c44b", "#fff0b3"),
        mono=False,
        icons=True,
    ),
    "matrix": Theme(
        name="matrix",
        accent="#3ddc84",
        danger="#ff5f56",
        ok="#3ddc84",
        warn="#a8e05f",
        dim="#2f5d3a",
        ramp=("#0d3b1e", "#1f7a3d", "#3ddc84", "#c8ffd8"),
        mono=False,
        icons=True,
    ),
    "dusk": Theme(
        name="dusk",
        accent="#7dd3fc",
        danger="#fca5a5",
        ok="#86efac",
        warn="#fcd34d",
        dim="#64748b",
        # The bar travels cool to warm as it fills: starting, moving, nearly
        # there, arrived. The counters reuse the same colours, so one glance
        # reads the same way everywhere.
        ramp=("#4c5b8a", "#7dd3fc", "#86efac", "#d9f99d"),
        mono=False,
        icons=True,
    ),
    "mono": Theme(
        name="mono",
        accent="#ffffff",
        danger="#ffffff",
        ok="#ffffff",
        warn="#ffffff",
        dim="#999999",
        ramp=("#ffffff", "#ffffff"),
        mono=True,
        icons=False,
    ),
}

DEFAULT = "aurora"


def select(cfg: Config, env: dict[str, str] | None = None) -> Theme:
    environ = os.environ if env is None else env
    if environ.get("NO_COLOR") or environ.get("TERM") == "dumb":
        return THEMES["mono"]
    return THEMES.get(cfg.general.theme, THEMES[DEFAULT])


GLYPHS = {
    "✅": "[ok]",
    "❌": "[fail]",
    "⏳": "[..]",
    "⏭": "[skip]",
    "♻️": "[replace]",
    "🌐": "[proxy]",
    "⚙": "*",
    "⚠": "!",
    "‼️": "!!",
    "🕘": "[recent]",
    "📥": "[dir]",
    "▶": ">",
    "⏸": "||",
    "🚀": ">>",
    "⏱": "~",
    "📂": "@",
    "⚙️": "*",
    "🚦": "!",
    "🪝": "^",
    "🏷️": "#",
    "🗂️": "=",
    "⬇": "v",
    "🔍": "/",
    "⇅": "sort",
}


def glyph(symbol: str, icons: bool) -> str:
    """The ASCII stand-in an emoji falls back to under the mono theme.

    Some terminal fonts draw emoji one cell wide instead of two, which shifts
    every column after them out of line. These stand-ins are unambiguous.
    """
    return symbol if icons else GLYPHS.get(symbol, symbol)


def icons_on(cfg: Config) -> bool:
    """For the places that hold a config rather than a resolved theme."""
    return select(cfg).icons


def icon_for(category: Category, theme: Theme) -> str:
    if theme.icons:
        return category.icon
    return category.name[:2].upper().ljust(2)


def category_icon(category: Category, cfg: Config) -> str:
    return icon_for(category, select(cfg))


def ramp_color(theme: Theme, position: float) -> str:
    if not theme.ramp:
        return theme.accent
    clamped = min(max(position, 0.0), 1.0)
    index = round(clamped * (len(theme.ramp) - 1))
    return theme.ramp[index]
