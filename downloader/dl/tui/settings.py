from dataclasses import replace
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .. import config as config_module
from .. import settings, theme as theme_module, tomlio
from ..format import pad
from ..config import Config

HINT = "↑↓ field   ←→ change   ⏎ edit   ^S save   esc cancel"
MENU_HINT = "↑↓ move   ⏎ open   esc close"
CYCLED = ("choice", "bool")

class IconMixin:
    """Every settings screen holds a cfg, so each can ask it about icons."""

    @property
    def _icons(self) -> bool:
        return theme_module.icons_on(self.cfg)

    def _g(self, symbol: str) -> str:
        return theme_module.glyph(symbol, self._icons)


class FormScreen(IconMixin, ModalScreen[dict]):
    """One screen for any list of scalar settings.

    Dismisses with the fields that changed, so the caller writes only those and
    a save never rewrites values the user did not touch.
    """

    # Nothing is focused until an edit starts: a focused Input swallows
    # printable keys as text, and most keys here are single letters.
    AUTO_FOCUS = ""

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous_field", "up", priority=True),
        Binding("down", "next_field", "down", priority=True),
        Binding("left", "previous_value", "left", priority=True),
        Binding("right", "next_value", "right", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, title: str, fields: tuple[settings.Field, ...], cfg: Config):
        super().__init__()
        self.title_text = title
        self.fields = fields
        self.cfg = cfg
        self.original = {
            f.path: settings.current(cfg, f) for f in fields if f.path in settings.ATTRIBUTE
        }
        self.values = dict(self.original)
        self.field = 0
        self.editing = False
        self.body = ""
        self.error = ""

    @property
    def input_value(self) -> str:
        return self.query_one("#settings-input", Input).value

    @input_value.setter
    def input_value(self, value: str) -> None:
        self.query_one("#settings-input", Input).value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static(f"  {self._g('⚙')}  {self.title_text}", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input")
            yield Static("", id="settings-error")
            yield Static(HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _current(self) -> settings.Field:
        return self.fields[self.field]

    def _repaint(self) -> None:
        rows = []
        for index, entry in enumerate(self.fields):
            marker = "▌" if index == self.field else " "
            shown = settings.render(self.values[entry.path])
            wrap = f"‹ {shown} ›" if entry.kind in CYCLED else shown
            rows.append(f"{marker} {pad(entry.label, 24)} {wrap}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def _move(self, delta: int) -> None:
        if self.editing:
            return
        self.field = (self.field + delta) % len(self.fields)
        self.error = ""
        self._repaint()

    def action_next_field(self) -> None:
        self._move(1)

    def action_previous_field(self) -> None:
        self._move(-1)

    def _cycle(self, delta: int) -> None:
        if self.editing:
            return
        entry = self._current()
        if entry.kind == "bool":
            self.values[entry.path] = not self.values[entry.path]
        elif entry.kind == "choice":
            options = entry.choices
            at = options.index(self.values[entry.path]) if self.values[entry.path] in options else 0
            self.values[entry.path] = options[(at + delta) % len(options)]
        else:
            return
        self._preview(entry)
        self._repaint()

    def action_next_value(self) -> None:
        self._cycle(1)

    def action_previous_value(self) -> None:
        self._cycle(-1)

    def _preview(self, entry: settings.Field) -> None:
        """Live fields are shown at once so they can be judged, but they are
        still only previews until the save."""
        if entry.live:
            self.app.reload_config(self._provisional())

    def _provisional(self) -> Config:
        general = replace(
            self.cfg.general,
            theme=self.values.get(("general", "theme"), self.cfg.general.theme),
        )
        return replace(self.cfg, general=general)

    def action_edit(self) -> None:
        entry = self._current()
        if entry.kind in CYCLED:
            return
        if not self.editing:
            self.editing = True
            box = self.query_one("#settings-input", Input)
            box.display = True
            box.value = settings.render(self.values[entry.path])
            box.focus()
            return
        self._commit(entry)

    def _commit(self, entry: settings.Field) -> None:
        try:
            self.values[entry.path] = settings.parse(entry, self.input_value)
        except settings.Invalid as exc:
            self.error = f"  {self._g('⚠')}  {entry.label}: {exc}"
            self._repaint()
            return
        self._close_editor()

    def _close_editor(self) -> None:
        self.editing = False
        self.error = ""
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._commit(self._current())

    def action_cancel(self) -> None:
        if self.editing:
            self._close_editor()
            return
        if any(f.live for f in self.fields):
            self.app.reload_config(self.cfg)
        self.dismiss({})

    def action_save(self) -> None:
        if self.editing:
            self._commit(self._current())
            return
        self.dismiss(
            {path: value for path, value in self.values.items() if value != self.original[path]}
        )


SECTIONS = (
    ("General", settings.GENERAL),
    ("Limits", settings.LIMITS),
    ("YouTube", settings.YOUTUBE),
    ("Hooks", settings.HOOKS),
)
LIST_ROWS = ("Proxy", "Headers", "Categories")
SECTION_ICONS = {
    "General": "⚙️",
    "Limits": "🚦",
    "YouTube": "🎬",
    "Hooks": "🪝",
    "Proxy": "🌐",
    "Headers": "🏷️",
    "Categories": "🗂️",
}


class SettingsMenuScreen(IconMixin, ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "close"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("enter", "open", "open", priority=True),
    ]

    def __init__(self, cfg: Config, config_file: Path):
        super().__init__()
        self.cfg = cfg
        self.config_file = config_file
        self.cursor = 0
        self.body = ""
        self.blocked = False
        self.problem = ""

    @property
    def rows(self) -> tuple[str, ...]:
        return tuple(name for name, _ in SECTIONS) + LIST_ROWS

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static(f"  {self._g('⚙')}  Settings", id="settings-head")
            yield Static("", id="settings-list")
            yield Static(MENU_HINT, id="settings-hint")

    def on_mount(self) -> None:
        try:
            tomlio.read(self.config_file)
        except tomlio.BrokenConfig as exc:
            # config.load() falls back to defaults on a broken file, so the app
            # is running on values the file does not contain. Saving those over
            # it would destroy whatever the user actually wrote.
            self.blocked = True
            self.problem = (
                f"  {self._g('⚠')}  config.toml has a syntax error on line {exc.line} —\n"
                f"     fix it before editing here. dl is running on defaults\n"
                f"     until it parses."
            )
        except OSError:
            pass
        self._repaint()

    def _repaint(self) -> None:
        if self.blocked:
            self.body = self.problem
        else:
            rows = []
            for index, name in enumerate(self.rows):
                marker = "▌" if index == self.cursor else " "
                icon = pad(self._g(SECTION_ICONS.get(name, "•")), 2)
                arrow = " ›" if name in LIST_ROWS else ""
                rows.append(f"{marker} {icon} {pad(name, 14)}{arrow}")
            self.body = "\n".join(rows) + "\n"
        self.query_one("#settings-list", Static).update(self.body)

    def _move(self, delta: int) -> None:
        if self.blocked:
            return
        self.cursor = (self.cursor + delta) % len(self.rows)
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_open(self) -> None:
        if self.blocked:
            return
        name = self.rows[self.cursor]
        for label, fields in SECTIONS:
            if label == name:
                self.app.push_screen(FormScreen(label, fields, self.cfg), self._saved)
                return
        if name == "Proxy":
            self.app.push_screen(ProxyScreen(self.cfg), self._saved)
        if name == "Headers":
            self.app.push_screen(HeadersScreen(self.cfg), self._saved)
        if name == "Categories":
            self.app.push_screen(CategoriesScreen(self.cfg), self._saved)

    def _saved(self, changes: dict | None) -> None:
        if changes:
            self.save(changes)

    def save(self, changes: dict) -> None:
        try:
            tomlio.apply(self.config_file, changes)
        except (OSError, tomlio.BrokenConfig) as exc:
            self.app.notify(f"could not save settings: {exc}", severity="error")
            return
        self.cfg = config_module.load(self.config_file)
        self.app.reload_config(self.cfg)
        self.app.notify("settings saved")

    def action_close(self) -> None:
        self.dismiss(None)


LIST_HINT = "↑↓ move   a add   d delete   ⏎ edit   u url   ^S save   esc cancel"
HEADER_HINT = "↑↓ move   a add   d delete   ^S save   esc cancel"
HEADER_FORM = "host | key | value"
CATEGORY_HINT = "↑↓ move   a add   d delete   ⏎ edit   ^S save   esc cancel"


class ListEditScreen(IconMixin, ModalScreen[dict]):
    """A list you walk with the arrows, add to, delete from and edit in place.

    The three list settings differ in what a row is and what saving one means.
    Everything around that — the cursor and its wrapping, clamping it after a
    delete, showing and hiding the one Input, cancelling an edit rather than
    the screen — was written out three times, and drifted: ^S while editing
    saved on one screen and did nothing on the others, and a refused entry
    closed the box on one and kept it open on the rest.

    Subclasses say what the rows are (lines, length), what a save returns
    (payload), what deleting does (remove_selected) and what to do with what
    was typed (commit). Enter on a row is start_edit, which does nothing
    unless a screen has something to do with it.
    """

    AUTO_FOCUS = ""
    heading = ""
    hint = LIST_HINT
    placeholder = ""

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("a", "add", "add", priority=True),
        Binding("d", "delete", "delete", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.cursor = 0
        # The mode being edited, or "" for not editing. A mode rather than a
        # flag because the proxy screen edits three different things.
        self.editing = ""
        self.body = ""
        self.error = ""

    def lines(self) -> list[str]:
        raise NotImplementedError

    def length(self) -> int:
        raise NotImplementedError

    def payload(self) -> dict:
        raise NotImplementedError

    def remove_selected(self) -> None:
        raise NotImplementedError

    def commit(self, value: str) -> None:
        """Take what was typed. Setting self.error refuses it."""
        raise NotImplementedError

    def start_edit(self) -> None:
        """Enter on a row."""

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static(f"  {self._g('⚙')}  {self.heading}", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input", placeholder=self.placeholder)
            yield Static("", id="settings-error")
            yield Static(self.hint, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _repaint(self) -> None:
        self.body = "\n".join(self.lines())
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def _open_editor(self, mode: str, prefill: str = "") -> None:
        self.editing = mode
        box = self.query_one("#settings-input", Input)
        box.display = True
        box.value = prefill
        box.focus()

    def _close_editor(self) -> None:
        self.editing = ""
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def _submit(self) -> None:
        self.error = ""
        self.commit(self.query_one("#settings-input", Input).value)
        if self.error:
            # Left open on the mistake, so it can be corrected rather than
            # retyped from the start.
            self._repaint()
            return
        self._close_editor()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def action_add(self) -> None:
        if not self.editing:
            self._open_editor("add")

    def action_edit(self) -> None:
        if self.editing:
            self._submit()
            return
        self.start_edit()

    def action_delete(self) -> None:
        if not self.editing:
            self.delete_selected()

    def delete_selected(self) -> None:
        if not self.length():
            return
        self.remove_selected()
        self.cursor = max(0, min(self.cursor, self.length() - 1))
        self._repaint()

    def _move(self, delta: int) -> None:
        if self.editing or not self.length():
            return
        self.cursor = (self.cursor + delta) % self.length()
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        if self.editing:
            self._close_editor()
            return
        self.dismiss({})

    def action_save(self) -> None:
        if self.editing:
            self._submit()
            return
        self.dismiss(self.payload())


class ProxyScreen(ListEditScreen):
    """The proxy URL, and the hosts always sent through it."""

    heading = "Proxy"
    BINDINGS = [*ListEditScreen.BINDINGS, Binding("u", "edit_url", "url", priority=True)]

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.url = cfg.proxy
        self.domains = list(cfg.proxy_domains)

    def length(self) -> int:
        return len(self.domains)

    def lines(self) -> list[str]:
        rows = [f"  URL   {self.url}", ""]
        if not self.domains:
            rows.append("  (no domains — every download goes direct unless -p)")
        for index, domain in enumerate(self.domains):
            marker = "▌" if index == self.cursor else " "
            rows.append(f"{marker} {domain}")
        return rows

    def payload(self) -> dict:
        return {("proxy", "url"): self.url, ("proxy", "domains"): list(self.domains)}

    def remove_selected(self) -> None:
        del self.domains[self.cursor]

    def start_edit(self) -> None:
        if self.domains:
            self._open_editor("edit", self.domains[self.cursor])

    def action_edit_url(self) -> None:
        if not self.editing:
            self._open_editor("url", self.url)

    def add_domain(self, value: str) -> None:
        text = value.strip().lower()
        if not text:
            self.error = f"  {self._g('⚠')}  a domain cannot be empty"
            self._repaint()
            return
        if text in self.domains:
            self.error = f"  {self._g('⚠')}  {text} is already listed"
            self._repaint()
            return
        self.domains.append(text)
        self.cursor = len(self.domains) - 1
        self.error = ""
        self._repaint()

    def commit(self, value: str) -> None:
        if self.editing == "url":
            text = value.strip()
            if not text:
                self.error = f"  {self._g('⚠')}  the proxy needs a URL"
                return
            self.url = text
        elif self.editing == "add":
            self.add_domain(value)
        elif self.editing == "edit" and self.domains:
            text = value.strip().lower()
            if not text:
                self.error = f"  {self._g('⚠')}  a domain cannot be empty"
                return
            self.domains[self.cursor] = text


class HeadersScreen(ListEditScreen):
    """Per-host request headers.

    TOML nests these two deep. The editor keeps them flat — one row per
    (host, key, value) — and rebuilds the nesting on save, so there is no
    second level to drill into for what is usually one line per site.
    """

    heading = "Headers"
    hint = HEADER_HINT
    placeholder = HEADER_FORM

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.rules = [
            (host, key, value)
            for host, fields in cfg.headers.items()
            for key, value in fields.items()
        ]

    def length(self) -> int:
        return len(self.rules)

    def lines(self) -> list[str]:
        if not self.rules:
            return ["  (no header rules)"]
        return [
            f"{'▌' if index == self.cursor else ' '} {pad(host, 26)} {pad(key, 16)} {value}"
            for index, (host, key, value) in enumerate(self.rules)
        ]

    def payload(self) -> dict:
        nested: dict[str, dict[str, str]] = {}
        for host, key, value in self.rules:
            nested.setdefault(host, {})[key] = value
        return {("headers",): nested}

    def remove_selected(self) -> None:
        del self.rules[self.cursor]

    def add_rule(self, raw: str) -> None:
        parts = [piece.strip() for piece in raw.split("|")]
        if len(parts) != 3 or not all(parts):
            self.error = f"  {self._g('⚠')}  write it as {HEADER_FORM}"
            self._repaint()
            return
        host, key, value = parts
        self.rules.append((host.lower(), key, value))
        self.cursor = len(self.rules) - 1
        self.error = ""
        self._repaint()

    def commit(self, value: str) -> None:
        self.add_rule(value)


class CategoriesScreen(ListEditScreen):
    """The categories that decide where a file lands.

    The built-in eight have no special status: the [categories] table is the
    whole list, so one can be edited or removed like any other.
    """

    heading = "Categories"
    hint = CATEGORY_HINT
    placeholder = "new category name"

    def __init__(self, cfg: Config):
        super().__init__(cfg)
        self.entries = {
            name: {
                "dir": str(category.dir),
                "ext": list(category.ext),
                "icon": category.icon,
                "hue": category.hue,
            }
            for name, category in cfg.categories.items()
        }

    @property
    def names(self) -> list[str]:
        return list(self.entries)

    def shown_ext(self, name: str) -> str:
        return ", ".join(self.entries[name]["ext"])

    def length(self) -> int:
        return len(self.entries)

    def lines(self) -> list[str]:
        rows = []
        for index, name in enumerate(self.names):
            marker = "▌" if index == self.cursor else " "
            entry = self.entries[name]
            shown = entry["icon"] if self._icons else name[:2].upper().ljust(2)
            rows.append(f"{marker} {pad(shown, 2)} {pad(name, 12)} {entry['dir']}")
        return rows

    def payload(self) -> dict:
        return {("categories",): {name: dict(entry) for name, entry in self.entries.items()}}

    def remove_selected(self) -> None:
        del self.entries[self.names[self.cursor]]

    def add_category(self, raw: str) -> None:
        name = raw.strip().lower()
        if not name:
            self.error = f"  {self._g('⚠')}  a category needs a name"
            self._repaint()
            return
        if name in self.entries:
            self.error = f"  {self._g('⚠')}  {name} already exists"
            self._repaint()
            return
        self.entries[name] = {
            "dir": str(self.cfg.general.default_dir / name),
            "ext": [],
            "icon": "📥",
            "hue": "#8a8a8a",
        }
        self.cursor = len(self.entries) - 1
        self.error = ""
        self._repaint()

    def commit(self, value: str) -> None:
        self.add_category(value)

    def apply_edit(self, name: str, changes: dict) -> None:
        for path, value in changes.items():
            key = path[-1]
            self.entries[name][key] = (
                [piece.strip() for piece in str(value).split(",") if piece.strip()]
                if key == "ext"
                else value
            )
        self._repaint()

    def start_edit(self) -> None:
        if not self.entries:
            return
        name = self.names[self.cursor]
        entry = self.entries[name]
        # The form reads its starting values from ATTRIBUTE, which only knows
        # top-level settings, so a category's four are seeded directly.
        screen = FormScreen(f"Category — {name}", settings.CATEGORY_FIELDS, self.cfg)
        screen.values = {
            ("dir",): entry["dir"],
            ("ext",): ", ".join(entry["ext"]),
            ("icon",): entry["icon"],
            ("hue",): entry["hue"],
        }
        screen.original = dict(screen.values)
        self.app.push_screen(screen, lambda changes: self.apply_edit(name, changes or {}))
