import subprocess
import time

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Static

from .. import cli, config, history, routing, theme
from ..config import STATE_DIR, Config
from ..format import human_bytes, human_duration
from ..rpc import Aria2Error, Aria2Unreachable
from .modals import AddUrlModal, ConfirmModal, SpeedLimitModal
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
AddUrlModal, SpeedLimitModal, ConfirmModal { align: center middle; }
#add-box, #limit-box, #confirm-box {
    width: 70; padding: 1 2; border: round $accent; background: $surface;
}
#urls { height: 8; }
"""

HINT = (
    "a add   space pause/resume   d delete   J K reorder   l limit   "
    "o open   tab completed   q quit"
)


class DlApp(App):
    CSS = CSS
    BINDINGS = [
        ("a", "add", "add"),
        ("space", "toggle", "pause/resume"),
        ("d", "delete", "delete"),
        ("J", "move_down", "down"),
        ("K", "move_up", "up"),
        ("l", "limit", "limit"),
        ("L", "limit_one", "limit one"),
        ("o", "reveal", "reveal"),
        ("p", "pause_all", "pause all"),
        ("u", "resume_all", "resume all"),
        ("r", "retry", "retry"),
        Binding("tab", "toggle_tab", "completed", priority=True),
        ("enter", "expand", "expand"),
        ("down", "cursor_down", "down"),
        ("up", "cursor_up", "up"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, cfg: Config, client):
        super().__init__()
        self.cfg = cfg
        self.client = client
        self.theme_data = theme.select(cfg)
        self.started = time.monotonic()
        self.showing_completed = False
        self.disconnected = False
        self.limit = cfg.limits.global_rate
        self.status = StatusBar(self.theme_data)
        self.table = DownloadTable(self.theme_data, id="table")
        self.completed = Static("", markup=True, id="completed")

    def compose(self) -> ComposeResult:
        yield self.status
        with VerticalScroll(id="body"):
            yield self.table
            yield self.completed
        yield Static(HINT, id="hint")

    def on_mount(self) -> None:
        self.completed.display = False
        self.set_interval(0.5, self.refresh_data)
        self.set_interval(0.1, self.table.refresh_view)
        self.call_after_refresh(self.refresh_data)

    async def refresh_data(self) -> None:
        try:
            items = list(self.client.tell_active()) + list(self.client.tell_waiting())
            stat = self.client.get_global_stat()
        except (Aria2Unreachable, Aria2Error):
            self.disconnected = True
            self.status.update(f"[{self.theme_data.danger}]⚠ daemon lost — reconnecting[/]")
            return
        self.disconnected = False
        self.table.set_rows([row_from_status(item, self.cfg) for item in items])
        elapsed = int(time.monotonic() - self.started)
        self.status.update_stats(stats_from(stat, self.limit, elapsed))
        if not items and not self.showing_completed:
            self.table.update(
                f"[{self.theme_data.accent}]{SPLASH}[/]\n   press a to add a download"
            )

    def _selected(self):
        gid = self.table.selected_gid
        if gid is None:
            return None
        return next((r for r in self.table.rows if r.gid == gid), None)

    def action_cursor_down(self) -> None:
        self.table.move(1)

    def action_cursor_up(self) -> None:
        self.table.move(-1)

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

    def action_reveal(self) -> None:
        row = self._selected()
        if row and row.path:
            subprocess.run(["open", "-R", str(row.path)], check=False)

    def action_toggle_tab(self) -> None:
        self.showing_completed = not self.showing_completed
        self.table.display = not self.showing_completed
        self.completed.display = self.showing_completed
        if self.showing_completed:
            self._render_completed()

    def _render_completed(self) -> None:
        rows = history.tail(STATE_DIR / "history.jsonl", 50)[::-1]
        lines = []
        for record in rows:
            mark = "✅" if record.get("status") == "ok" else "❌"
            age = human_duration(int(time.time()) - int(record.get("ts", 0) or 0))
            lines.append(
                f"  {mark}  {record.get('name', ''):<38} "
                f"{human_bytes(int(record.get('bytes', 0) or 0)):>10}  "
                f"{record.get('category', ''):<9} {age} ago"
            )
        self.completed.update("\n".join(lines) or "  (nothing finished yet)")

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
        def apply(rate: str | None) -> None:
            if rate is None:
                return
            value = config.parse_rate(rate)
            self.client.change_global_option({"max-overall-download-limit": value})
            self.limit = value

        self.push_screen(SpeedLimitModal(self.limit), apply)

    def action_limit_one(self) -> None:
        row = self._selected()
        if row is None:
            return

        def apply(rate: str | None) -> None:
            if rate is not None:
                self.client.change_option(row.gid, {"max-download-limit": config.parse_rate(rate)})

        self.push_screen(SpeedLimitModal("off"), apply)

    def action_delete(self) -> None:
        row = self._selected()
        if row is None:
            return

        def confirm(yes: bool) -> None:
            if yes:
                self.client.remove(row.gid)

        if row.done < row.total:
            self.push_screen(ConfirmModal(f"Delete {row.name}? It is incomplete."), confirm)
        else:
            self.client.remove(row.gid)


def run_tui(cfg: Config, client) -> int:
    DlApp(cfg, client).run()
    return 0
