"""The dashboard's furniture: its stylesheet, its key legend and its splash.

Kept apart from the app because none of it touches application state — it
turns a theme and a width into text, which is also what makes it testable
without starting a screen.
"""

from ..format import cells
from ..theme import glyph

EMPTY_KEYS = (("a", "add a download"), ("s", "settings"), ("q", "quit"))


def splash(theme_data) -> str:
    mark = glyph("⬇", theme_data.icons)
    lines = [
        "",
        f"  [{theme_data.accent}]{mark}  dl[/]  [{theme_data.dim}]· download manager[/]",
        "",
        f"  [{theme_data.dim}]Nothing downloading yet.[/]",
        "",
    ]
    lines += [
        f"  [{theme_data.accent}]{key}[/]  [{theme_data.dim}]{label}[/]"
        for key, label in EMPTY_KEYS
    ]
    return "\n".join(lines)


def hint_pairs_for(pairs, width: int):
    """Drop from the right until it fits. The order the keys are declared in is
    the order worth keeping, so a narrow terminal loses `quit` before `add`."""
    kept = list(pairs)
    while kept and cells("  " + "   ".join(f"{k} {v}" for k, v in kept)) > width:
        kept.pop()
    return kept


def render_hint(pairs, theme_data, width: int = 200) -> str:
    """Keys carry the accent, labels stay quiet — the bar is a legend, not a
    sentence, and every pair keeps the same gap."""
    kept = hint_pairs_for(pairs, width)
    if theme_data.mono:
        return "  " + "   ".join(f"{key} {label}" for key, label in kept)
    return "  " + "   ".join(
        f"[{theme_data.accent}]{key}[/] [{theme_data.dim}]{label}[/]" for key, label in kept
    )


CSS = """
Screen { layout: vertical; }
StatusBar { height: 1; dock: top; padding: 0 1; }
#body { height: 1fr; padding: 0 1; }
/* One docked block, so a note appearing pushes the legend up rather than off
   the bottom of the screen. */
#footer { dock: bottom; height: auto; }
#hint { height: 1; padding: 0 1; color: $dl-dim; }
#search-note { height: 1; padding: 0 1; }
#search-input { dock: bottom; height: 3; margin: 0 1; }

AddUrlModal, SpeedLimitModal, ConfirmModal, DeleteModal, PickerScreen, DuplicateModal,
SettingsMenuScreen, FormScreen, ProxyScreen, HeadersScreen, CategoriesScreen,
PlaylistScreen {
    align: center middle;
    background: $dl-veil;
}

#add-box, #limit-box, #confirm-box, #delete-box, #picker-box, #duplicate-box,
#settings-box, #playlist-box {
    width: 72;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: round $dl-accent;
    background: $dl-surface;
}

#add-box Label, #limit-box Label, #confirm-box Label, #delete-box Label,
#duplicate-head, #picker-head, #settings-head, #playlist-head {
    text-style: bold; color: $dl-accent;
}

#duplicate-detail, #picker-list, #picker-error, #settings-list, #settings-error,
#playlist-detail {
    height: auto;
    color: $dl-text;
}

Button {
    width: 100%;
    height: 1;
    margin-top: 1;
    border: none;
    background: $dl-quiet;
    color: $dl-text;
    text-style: none;
}
Button:hover { background: $dl-accent; color: $dl-surface; }
Button:focus { background: $dl-accent; color: $dl-surface; text-style: bold; }
Button.-error, Button#disk, Button#overwrite {
    background: $dl-quiet;
    color: $dl-danger;
}
Button.-error:focus, Button#disk:focus, Button#overwrite:focus {
    background: $dl-danger;
    color: $dl-surface;
}

Input, TextArea {
    border: round $dl-quiet;
    background: $dl-surface;
    color: $dl-text;
}
Input:focus, TextArea:focus { border: round $dl-accent; }

#urls { height: 6; }
#settings-input { margin-top: 1; }
"""

HINT_KEYS = (
    ("a", "add"),
    ("space", "pause"),
    ("d", "delete"),
    ("J K", "move"),
    ("l", "limit"),
    ("o", "open"),
    ("f", "finder"),
    ("s", "settings"),
    ("y", "copy url"),
    ("/", "search"),
    ("S R", "sort"),
    ("tab", "done"),
    ("q", "quit"),
)
DONE_KEYS = (
    ("o", "open"),
    ("f", "finder"),
    ("d", "delete"),
    ("↑↓", "move"),
    ("r", "again"),
    ("y", "copy url"),
    ("/", "search"),
    ("S R", "sort"),
    ("tab", "active"),
    ("q", "quit"),
)
HINT = "  ".join(f"{k} {v}" for k, v in HINT_KEYS)
HINT_DONE = "  ".join(f"{k} {v}" for k, v in DONE_KEYS)
