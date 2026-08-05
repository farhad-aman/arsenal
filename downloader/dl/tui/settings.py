from dataclasses import replace
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .. import config as config_module
from .. import settings, tomlio
from ..config import Config

HINT = "↑↓ field   ←→ change   ⏎ edit   ^S save   esc cancel"
MENU_HINT = "↑↓ move   ⏎ open   esc close"
CYCLED = ("choice", "bool")


class FormScreen(ModalScreen[dict]):
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
            yield Static(f"  ⚙  {self.title_text}", id="settings-head")
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
            rows.append(f"{marker} {entry.label:<24} {wrap}")
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
            ascii_icons=self.values.get(("general", "ascii_icons"), self.cfg.general.ascii_icons),
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
            self.error = f"  ⚠  {entry.label}: {exc}"
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


class SettingsMenuScreen(ModalScreen[None]):
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
            yield Static("  ⚙  Settings", id="settings-head")
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
                f"  ⚠  config.toml has a syntax error on line {exc.line} —\n"
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
                arrow = "  ›" if name in LIST_ROWS else ""
                rows.append(f"{marker} {name}{arrow}")
            self.body = "\n".join(rows)
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


class ProxyScreen(ModalScreen[dict]):
    """The proxy URL, and the hosts always sent through it."""

    AUTO_FOCUS = ""

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("a", "add", "add", priority=True),
        Binding("d", "delete", "delete", priority=True),
        Binding("u", "edit_url", "url", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.url = cfg.proxy
        self.domains = list(cfg.proxy_domains)
        self.cursor = 0
        self.editing = ""
        self.body = ""
        self.error = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("  ⚙  Proxy", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input")
            yield Static("", id="settings-error")
            yield Static(LIST_HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _repaint(self) -> None:
        rows = [f"  URL   {self.url}", ""]
        if not self.domains:
            rows.append("  (no domains — every download goes direct unless -p)")
        for index, domain in enumerate(self.domains):
            marker = "▌" if index == self.cursor else " "
            rows.append(f"{marker} {domain}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def add_domain(self, value: str) -> None:
        text = value.strip().lower()
        if not text:
            self.error = "  ⚠  a domain cannot be empty"
            self._repaint()
            return
        if text in self.domains:
            self.error = f"  ⚠  {text} is already listed"
            self._repaint()
            return
        self.domains.append(text)
        self.cursor = len(self.domains) - 1
        self.error = ""
        self._repaint()

    def delete_selected(self) -> None:
        if not self.domains:
            return
        del self.domains[self.cursor]
        self.cursor = max(0, min(self.cursor, len(self.domains) - 1))
        self._repaint()

    def _open_editor(self, mode: str, prefill: str) -> None:
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

    def action_add(self) -> None:
        if not self.editing:
            self._open_editor("add", "")

    def action_edit_url(self) -> None:
        if not self.editing:
            self._open_editor("url", self.url)

    def action_edit(self) -> None:
        if self.editing:
            self._submit()
            return
        if self.domains:
            self._open_editor("edit", self.domains[self.cursor])

    def action_delete(self) -> None:
        if not self.editing:
            self.delete_selected()

    def _submit(self) -> None:
        value = self.query_one("#settings-input", Input).value
        if self.editing == "url":
            text = value.strip()
            if not text:
                self.error = "  ⚠  the proxy needs a URL"
                self._repaint()
                return
            self.url = text
        elif self.editing == "add":
            self.add_domain(value)
        elif self.editing == "edit" and self.domains:
            text = value.strip().lower()
            if not text:
                self.error = "  ⚠  a domain cannot be empty"
                self._repaint()
                return
            self.domains[self.cursor] = text
        self._close_editor()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _move(self, delta: int) -> None:
        if self.editing or not self.domains:
            return
        self.cursor = (self.cursor + delta) % len(self.domains)
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
        self.dismiss({("proxy", "url"): self.url, ("proxy", "domains"): list(self.domains)})
