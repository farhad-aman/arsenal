import asyncio
import subprocess
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import cli, config, history, routing, theme
from ..config import STATE_DIR, Config
from ..rpc import Aria2Error, Aria2Unreachable
from .completed import CompletedTable, record_path
from .modals import AddUrlModal, DeleteModal, SpeedLimitModal
from .status import StatusBar, stats_from
from .table import DownloadTable, row_from_status

SPLASH = """\
                    ██████╗ ██╗
                    ██╔══██╗██║        d o w n l o a d e r
                    ██║  ██║██║        ─────────────────────
                    ██████╔╝███████╗   ⚡ powered by aria2
                    ╚═════╝ ╚══════╝
                         ▼ ▼ ▼
"""

CSS = """
Screen { layout: vertical; }
StatusBar { height: 1; dock: top; padding: 0 1; }
#body { height: 1fr; padding: 0 1; }
#hint { dock: bottom; height: 1; padding: 0 1; }
AddUrlModal, SpeedLimitModal, ConfirmModal, DeleteModal, PickerScreen, DuplicateModal {
    align: center middle;
}
#add-box, #limit-box, #confirm-box, #delete-box, #picker-box, #duplicate-box {
    width: 76; padding: 1 2; border: round $accent; background: $surface;
}
#duplicate-box Button { width: 100%; margin-top: 1; }
#duplicate-head { text-style: bold; }
#duplicate-detail { height: auto; margin-top: 1; }
#picker-list { height: auto; }
#picker-error { height: auto; }
#picker-head { text-style: bold; }
#delete-box Button { width: 100%; margin-top: 1; }
#urls { height: 8; }
"""

HINT = (
    "a add   space pause/resume   d delete   J K reorder   l limit   "
    "o open   f finder   tab completed   q quit"
)
HINT_DONE = "o open   f finder   d delete   ↑↓ move   tab active   q quit"

SETTLED = ("removed", "error", "complete")
SETTLE_TIMEOUT = 5.0


class DlApp(App):
    CSS = CSS
    BINDINGS = [
        ("a", "add", "add"),
        ("space", "toggle", "pause/resume"),
        ("d", "delete", "delete"),
        ("J", "move_down", "down"),
        ("K", "move_up", "up"),
        ("l", "limit", "speed limit"),
        ("o", "open", "open"),
        ("f", "reveal", "reveal in finder"),
        ("p", "pause_all", "pause all"),
        ("u", "resume_all", "resume all"),
        ("r", "retry", "retry"),
        Binding("tab", "toggle_tab", "completed", priority=True),
        ("enter", "expand", "expand"),
        ("down", "cursor_down", "down"),
        ("up", "cursor_up", "up"),
        ("q", "quit", "quit"),
    ]

    splash_when_empty = True

    def __init__(self, cfg: Config, client):
        super().__init__()
        self.cfg = cfg
        self.client = client
        self.theme_data = theme.select(cfg)
        self.started = time.monotonic()
        self.showing_completed = False
        self.disconnected = False
        self.proxied: dict[str, bool] = {}
        self.status = StatusBar(self.theme_data)
        self.table = DownloadTable(self.theme_data, id="table")
        self.completed = CompletedTable(self.theme_data, id="completed")
        self.hint_text = HINT
        self.hint = Static(HINT, id="hint")

    def compose(self) -> ComposeResult:
        yield self.status
        with VerticalScroll(id="body"):
            yield self.table
            yield self.completed
        yield self.hint

    def on_mount(self) -> None:
        self.completed.display = False
        self.set_interval(0.5, self.refresh_data)
        self.set_interval(0.1, self.table.refresh_view)
        self.call_after_refresh(self.refresh_data)

    def check_action(self, action: str, parameters: tuple) -> bool:
        """Priority bindings are resolved app-first, so a dashboard key would beat
        the modal on top of it. Standing down lets the modal's binding run."""
        if isinstance(self.screen, ModalScreen):
            return False
        return True

    def _proxy_flags(self, items: list[dict]) -> None:
        """A download's options never change under us, so each gid is asked
        about once — the table refreshes twice a second."""
        live = {item.get("gid", "") for item in items}
        for gid in live - self.proxied.keys():
            if not gid:
                continue
            try:
                options = self.client.get_option(gid)
            except (Aria2Error, Aria2Unreachable):
                continue
            self.proxied[gid] = bool(options.get("all-proxy") or options.get("http-proxy"))
        self.proxied = {gid: flag for gid, flag in self.proxied.items() if gid in live}

    def _filter_items(self, items: list[dict]) -> list[dict]:
        return items

    def _after_refresh(self, items: list[dict]) -> None:
        return None

    async def refresh_data(self) -> None:
        try:
            polled = list(self.client.tell_active()) + list(self.client.tell_waiting())
            stat = self.client.get_global_stat()
        except (Aria2Unreachable, Aria2Error):
            self.disconnected = True
            self.status.update(f"[{self.theme_data.danger}]⚠ daemon lost — reconnecting[/]")
            return
        self.disconnected = False
        items = self._filter_items(polled)
        self._proxy_flags(items)
        self.table.set_rows(
            [
                row_from_status(item, self.cfg, self.proxied.get(item.get("gid", ""), False))
                for item in items
            ]
        )
        elapsed = int(time.monotonic() - self.started)
        self.status.update_stats(stats_from(stat, elapsed))
        if not items and self.splash_when_empty and not self.showing_completed:
            self.table.update(
                f"[{self.theme_data.accent}]{SPLASH}[/]\n   press a to add a download"
            )
        self._after_refresh(items)

    def _selected(self):
        gid = self.table.selected_gid
        if gid is None:
            return None
        return next((r for r in self.table.rows if r.gid == gid), None)

    @property
    def history_log(self):
        return STATE_DIR / "history.jsonl"

    def _active_widget(self):
        return self.completed if self.showing_completed else self.table

    def _selected_path(self):
        if self.showing_completed:
            record = self.completed.selected
            return record_path(record) if record else None
        row = self._selected()
        return row.path if row and row.path else None

    def action_cursor_down(self) -> None:
        self._active_widget().move(1)

    def action_cursor_up(self) -> None:
        self._active_widget().move(-1)

    def action_expand(self) -> None:
        self.table.expanded = not self.table.expanded
        self.table.refresh_view()

    def action_toggle(self) -> None:
        row = self._selected()
        if row is None:
            return
        if row.status == "paused":
            self.client.unpause(row.gid)
        else:
            self.client.pause(row.gid)

    def action_pause_all(self) -> None:
        for row in self.table.rows:
            self.client.pause(row.gid)

    def action_resume_all(self) -> None:
        for row in self.table.rows:
            self.client.unpause(row.gid)

    def action_move_down(self) -> None:
        row = self._selected()
        if row:
            self.client.change_position(row.gid, 1, "POS_CUR")

    def action_move_up(self) -> None:
        row = self._selected()
        if row:
            self.client.change_position(row.gid, -1, "POS_CUR")

    def action_retry(self) -> None:
        row = self._selected()
        if row is None or row.status != "error":
            return
        resolution = routing.resolve("", row.name, self.cfg)
        self.client.add_uri([str(row.path)], cli.add_options(self.cfg, resolution))

    def action_open(self) -> None:
        path = self._selected_path()
        if path and path.exists():
            subprocess.run(["open", str(path)], check=False)
        elif path:
            self.notify(f"{path.name} is not on disk", severity="warning")

    def action_reveal(self) -> None:
        path = self._selected_path()
        if path and path.exists():
            subprocess.run(["open", "-R", str(path)], check=False)
        elif path:
            self.notify(f"{path.name} is not on disk", severity="warning")

    def action_toggle_tab(self) -> None:
        self.showing_completed = not self.showing_completed
        self.table.display = not self.showing_completed
        self.completed.display = self.showing_completed
        self.hint_text = HINT_DONE if self.showing_completed else HINT
        self.hint.update(self.hint_text)
        if self.showing_completed:
            self.completed.load(self.history_log)

    def action_add(self) -> None:
        def queue(urls: list[str] | None) -> None:
            if not urls:
                return
            for url in urls:
                name = routing.filename_from_url(url)
                resolution = routing.resolve(url, name, self.cfg)
                resolution.path.mkdir(parents=True, exist_ok=True)
                self.client.add_uri([url], cli.add_options(self.cfg, resolution))

        self.push_screen(AddUrlModal(), queue)

    def action_limit(self) -> None:
        row = self._selected()
        if row is None:
            return

        def apply(rate: str | None) -> None:
            if rate is None:
                return
            value = config.parse_rate(rate)
            self.client.change_option(row.gid, {"max-download-limit": value})
            self.notify(f"{row.name}: limit {'off' if value == '0' else value}")

        self.push_screen(SpeedLimitModal(self.cfg.limits.per_download), apply)

    def action_delete(self) -> None:
        if self.showing_completed:
            self._delete_completed()
        else:
            self._delete_active()

    def _unlink(self, path) -> None:
        for target in (path, path.with_name(path.name + ".aria2")):
            try:
                target.unlink()
            except OSError:
                pass

    async def _settle_then_unlink(self, gid: str, path: Path) -> None:
        """aria2 rewrites the control file while winding a download down, so a
        sidecar deleted the instant remove() returns comes straight back."""
        deadline = time.monotonic() + SETTLE_TIMEOUT
        while time.monotonic() < deadline:
            try:
                status = self.client.tell_status(gid).get("status", "")
            except (Aria2Error, Aria2Unreachable):
                break
            if status in SETTLED:
                break
            await asyncio.sleep(0.05)
        self._unlink(path)

    def _delete_active(self) -> None:
        row = self._selected()
        if row is None:
            return
        has_file = bool(row.path) and row.path.exists()

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            try:
                self.client.remove(row.gid)
            except (Aria2Error, Aria2Unreachable):
                pass
            if choice == "disk" and row.path:
                self.run_worker(self._settle_then_unlink(row.gid, row.path))
                self.notify(f"deleted {row.name}")

        self.push_screen(DeleteModal(row.name or row.gid, has_file), chosen)

    def _delete_completed(self) -> None:
        record = self.completed.selected
        if record is None:
            return
        path = record_path(record)
        has_file = bool(path and path.exists())

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            history.remove_entry(self.history_log, record)
            if choice == "disk" and path:
                self._unlink(path)
            self.completed.load(self.history_log)
            self.notify(
                f"removed {record.get('name', '')}"
                + (" and its file" if choice == "disk" else " from the list")
            )

        self.push_screen(DeleteModal(record.get("name", "") or "entry", has_file), chosen)


def run_tui(cfg: Config, client) -> int:
    DlApp(cfg, client).run()
    return 0
