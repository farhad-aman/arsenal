import asyncio
import subprocess
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static

from .. import cli, config, duplicates, history, routing, search, theme, ytjob
from ..theme import glyph
from ..config import CONFIG_FILE, STATE_DIR, Config
from ..format import cells, human_bytes
from ..rpc import Aria2Error, Aria2Unreachable
from .completed import CompletedTable, record_path
from . import ytflow
from .modals import AddUrlModal, DeleteModal, DuplicateModal, SpeedLimitModal
from .searchbar import INPUT_ID, NOTE_ID, SearchCancelled, SearchInput, SearchNote, empty_note
from .status import StatusBar, stats_from
from .table import DownloadTable, is_youtube_row, row_from_job, row_from_status

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
#hint { dock: bottom; height: 1; padding: 0 1; color: $dl-dim; }
#search-note { dock: bottom; height: 1; padding: 0 1; }
#search-input { dock: bottom; height: 3; margin: 0 1; }

AddUrlModal, SpeedLimitModal, ConfirmModal, DeleteModal, PickerScreen, DuplicateModal,
SettingsMenuScreen, FormScreen, ProxyScreen, HeadersScreen, CategoriesScreen {
    align: center middle;
    background: $dl-veil;
}

#add-box, #limit-box, #confirm-box, #delete-box, #picker-box, #duplicate-box, #settings-box {
    width: 72;
    height: auto;
    max-height: 80%;
    padding: 1 2;
    border: round $dl-accent;
    background: $dl-surface;
}

#add-box Label, #limit-box Label, #confirm-box Label, #delete-box Label,
#duplicate-head, #picker-head, #settings-head { text-style: bold; color: $dl-accent; }

#duplicate-detail, #picker-list, #picker-error, #settings-list, #settings-error {
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
    ("/", "search"),
    ("tab", "done"),
    ("q", "quit"),
)
DONE_KEYS = (
    ("o", "open"),
    ("f", "finder"),
    ("d", "delete"),
    ("↑↓", "move"),
    ("/", "search"),
    ("tab", "active"),
    ("q", "quit"),
)
HINT = "  ".join(f"{k} {v}" for k, v in HINT_KEYS)
HINT_DONE = "  ".join(f"{k} {v}" for k, v in DONE_KEYS)

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
        ("s", "settings", "settings"),
        ("slash", "search", "search"),
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
        self.hint = Static(render_hint(HINT_KEYS, self.theme_data), id="hint", markup=True)
        self.search_query = ""
        self.search_total = 0
        self.search_note = SearchNote(self.theme_data, id=NOTE_ID)
        self.search_input: SearchInput | None = None

    def get_css_variables(self) -> dict[str, str]:
        """Textual's stock palette is blue and orange, which is why the modals
        looked like a different program. Feeding dl's own theme in means the
        chrome follows whichever theme is chosen."""
        # Textual asks for these during App.__init__, before our theme exists.
        chosen = getattr(self, "theme_data", None) or theme.THEMES[theme.DEFAULT]
        return {
            **super().get_css_variables(),
            "dl-accent": chosen.accent,
            "dl-danger": chosen.danger,
            "dl-ok": chosen.ok,
            "dl-warn": chosen.warn,
            "dl-dim": chosen.dim,
            "dl-text": "#d7dae0" if not chosen.mono else "#ffffff",
            "dl-surface": "#15171c" if not chosen.mono else "#000000",
            "dl-quiet": "#232833" if not chosen.mono else "#222222",
            "dl-veil": "rgba(8,10,14,0.65)",
        }

    def compose(self) -> ComposeResult:
        yield self.status
        with VerticalScroll(id="body"):
            yield self.table
            yield self.completed
        yield self.hint
        yield self.search_note

    def _repaint_hint(self) -> None:
        pairs = DONE_KEYS if self.showing_completed else HINT_KEYS
        self.hint.update(render_hint(pairs, self.theme_data, self.size.width or 100))

    def on_resize(self, _event) -> None:
        self._repaint_hint()

    def on_mount(self) -> None:
        # Textual reads the CSS variables inside App.__init__, before the line
        # that sets theme_data has run, so the stylesheet starts out holding
        # the fallback theme. Re-resolve now that the real one exists.
        self.refresh_css()
        self._repaint_hint()
        ytjob.sweep(STATE_DIR / "yt", self.history_log)
        self.completed.display = False
        self.search_note.display = False
        self.set_interval(0.5, self.refresh_data)
        self.set_interval(0.1, self.table.refresh_view)
        self.call_after_refresh(self.refresh_data)

    def check_action(self, action: str, parameters: tuple) -> bool:
        """Priority bindings are resolved app-first, so a dashboard key would beat
        the modal on top of it. Standing down lets the modal's binding run.

        The same applies while the search box is open: `d` there is a letter
        being typed, not the delete key.
        """
        if isinstance(self.screen, ModalScreen):
            return False
        return not self.searching

    def reload_config(self, cfg: Config) -> None:
        """Adopt a freshly read config.

        Only max_concurrent reaches the daemon. The rest of the limits are set
        per-download at queue time, so pushing them globally would change
        behaviour for downloads dl did not queue.
        """
        was = self.cfg
        self.cfg = cfg
        self.theme_data = theme.select(cfg)
        for widget in (self.status, self.table, self.completed, self.search_note):
            widget.theme_data = self.theme_data
        self.table.refresh_view()
        self._repaint_hint()
        self.refresh_css()
        if cfg.general.max_concurrent != was.general.max_concurrent:
            try:
                self.client.change_global_option(
                    {"max-concurrent-downloads": str(cfg.general.max_concurrent)}
                )
            except (Aria2Error, Aria2Unreachable) as exc:
                self.notify(f"saved, but the daemon did not take it: {exc}", severity="warning")

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

    @property
    def searching(self) -> bool:
        return self.search_input is not None

    def action_search(self) -> None:
        """Mounted only while it is open.

        A hidden Input that lives in the DOM takes auto-focus and then eats d,
        l and u as typed text. Turning auto-focus off app-wide would reach the
        modals too, whose inputs do need it.
        """
        if self.searching:
            return
        self.search_input = SearchInput(
            value=self.search_query, placeholder="filter by name", id=INPUT_ID
        )
        self.mount(self.search_input)
        self.search_input.focus()
        self._repaint_search()

    def on_input_changed(self, event) -> None:
        if event.input.id != INPUT_ID:
            return
        self.search_query = event.value
        self._reload_completed()
        self._repaint_search()

    def on_input_submitted(self, event) -> None:
        """Enter puts the keyboard back on the list without dropping the filter."""
        if event.input.id != INPUT_ID:
            return
        self._close_search()

    def on_search_cancelled(self, _event: SearchCancelled) -> None:
        self.search_query = ""
        self._reload_completed()
        self._close_search()

    def _close_search(self) -> None:
        if self.search_input is not None:
            self.search_input.remove()
            self.search_input = None
        self.set_focus(None)
        self._repaint_search()

    def _repaint_search(self) -> None:
        on = search.active(self.search_query)
        self.search_note.display = on
        if not on:
            return
        if self.showing_completed:
            self.search_note.show(self.search_query, len(self.completed.rows), None)
        else:
            self.search_note.show(self.search_query, len(self.table.rows), self.search_total)

    def _reload_completed(self) -> None:
        if self.showing_completed:
            self.completed.load(self.history_log, self.search_query)

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
        rows = [
            row_from_status(item, self.cfg, self.proxied.get(item.get("gid", ""), False))
            for item in items
        ]
        rows += [row_from_job(job, self.cfg) for job in self._youtube_jobs()]
        self.search_total = len(rows)
        # Before set_rows, which is what draws the placeholder. Setting it after
        # leaves the message a frame behind the query it is describing.
        # Before set_rows, which is what draws the placeholder. Setting it after
        # leaves the message a frame behind the query it is describing.
        if search.active(self.search_query):
            self.table.placeholder = empty_note(self.search_query, self.theme_data)
        elif self.splash_when_empty and not self.showing_completed:
            self.table.placeholder = splash(self.theme_data)
        self.table.set_rows(search.keep(rows, self.search_query, lambda row: row.name))
        elapsed = int(time.monotonic() - self.started)
        self.status.update_stats(stats_from(stat, elapsed))
        self._repaint_search()
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
        if is_youtube_row(row):
            self._toggle_youtube(row)
        elif row.status == "paused":
            self.client.unpause(row.gid)
        else:
            self.client.pause(row.gid)

    def action_pause_all(self) -> None:
        for row in self.table.rows:
            if is_youtube_row(row):
                self._pause_youtube(row)
            else:
                self.client.pause(row.gid)

    def action_resume_all(self) -> None:
        for row in self.table.rows:
            if is_youtube_row(row):
                if row.status == "paused":
                    self._resume_youtube(row)
            else:
                self.client.unpause(row.gid)

    def _job_for(self, row) -> dict | None:
        return next(
            (j for j in ytjob.list_jobs(STATE_DIR / "yt") if j.get("id") == row.gid), None
        )

    def _toggle_youtube(self, row) -> None:
        if row.status == "paused":
            self._resume_youtube(row)
        else:
            self._pause_youtube(row)

    def _pause_youtube(self, row) -> None:
        job = self._job_for(row)
        if job is not None:
            ytjob.pause(STATE_DIR / "yt", job)

    def _resume_youtube(self, row) -> None:
        job = self._job_for(row)
        if job is not None:
            ytflow.resume(job, STATE_DIR)

    def action_move_down(self) -> None:
        self._reorder(1)

    def action_move_up(self) -> None:
        self._reorder(-1)

    def _reorder(self, offset: int) -> None:
        row = self._selected()
        if row is None:
            return
        if is_youtube_row(row):
            self.notify("YouTube downloads start at once — there is no queue to move in")
            return
        self.client.change_position(row.gid, offset, "POS_CUR")

    def action_retry(self) -> None:
        row = self._selected()
        if row is None or row.status != "error":
            return
        if is_youtube_row(row):
            self._retry_youtube(row)
            return
        if not row.url:
            self.notify(f"{row.name}: no source URL to retry", severity="warning")
            return
        resolution = routing.resolve(row.url, row.name, self.cfg)
        try:
            self.client.add_uri(
                [row.url],
                cli.add_options(
                    self.cfg,
                    resolution,
                    routing.through_proxy(row.url, self.cfg, forced=row.proxied),
                    None,
                    routing.header_lines(routing.headers_for(row.url, self.cfg)),
                ),
            )
        except (Aria2Error, Aria2Unreachable) as exc:
            self.notify(f"retry failed: {exc}", severity="error")
            return
        self.notify(f"retrying {row.name}")

    def _retry_youtube(self, row) -> None:
        job_file = STATE_DIR / "yt" / f"{row.gid}.json"
        try:
            job = ytjob.read(job_file)
        except (OSError, ValueError):
            self.notify(f"{row.name}: job record is gone", severity="warning")
            return
        job.update(status="queued", error="", done=0, speed=0, pid=0)
        ytjob.save(STATE_DIR / "yt", job)
        ytflow.spawn(job, STATE_DIR)
        self.notify(f"retrying {Path(job['url']).name or job['url']}")

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
        self._repaint_hint()
        if self.showing_completed:
            self.completed.load(self.history_log, self.search_query)
        self._repaint_search()

    def action_add(self) -> None:
        def queue(urls: list[str] | None) -> None:
            if urls:
                self._queue_next(list(urls), 0)

        self.push_screen(AddUrlModal(), queue)

    def action_settings(self) -> None:
        from .settings import SettingsMenuScreen

        self.push_screen(SettingsMenuScreen(self.cfg, CONFIG_FILE))

    def _youtube_jobs(self) -> list[dict]:
        """yt-dlp downloads, which the aria2 daemon knows nothing about.

        Failures stay on the list until they are deleted: a job that vanished
        on error would look exactly like one that never started.
        """
        jobs = [ytjob.reap(STATE_DIR / "yt", job) for job in ytjob.list_jobs(STATE_DIR / "yt")]
        return self._filter_jobs(
            [
                job
                for job in jobs
                if job.get("status") in ("queued", "active", "burning", "paused", "error")
            ]
        )

    def _filter_jobs(self, jobs: list[dict]) -> list[dict]:
        return jobs

    def _in_flight(self) -> list[dict]:
        try:
            return list(self.client.tell_active()) + list(self.client.tell_waiting())
        except (Aria2Error, Aria2Unreachable):
            return []

    def _queue_next(self, urls: list[str], index: int) -> None:
        """Queue one URL at a time so a collision can be asked about before the
        next one is considered."""
        if index >= len(urls):
            return
        url = urls[index]
        name = routing.filename_from_url(url)
        resolution = routing.resolve(url, name, self.cfg)
        target = resolution.path / name if name else None
        collision = duplicates.detect(
            url, target, history.tail(self.history_log, 200), self._in_flight()
        )
        if collision is None:
            self._queue_one(url, resolution, None, target)
            self._queue_next(urls, index + 1)
            return

        def decided(choice: str | None) -> None:
            """Escape declines this one and moves on — the dashboard has no
            batch to abandon."""
            if choice is not None:
                self._queue_one(url, resolution, choice, target)
            self._queue_next(urls, index + 1)

        self.push_screen(
            DuplicateModal(name or url, collision, human_bytes(collision.size)), decided
        )

    def _queue_one(self, url: str, resolution, decision: str | None, target: Path | None) -> None:
        if decision == duplicates.SKIP:
            self.notify(f"skipped {resolution.path.name or url}")
            return
        resolution.path.mkdir(parents=True, exist_ok=True)
        options = cli.add_options(
            self.cfg,
            resolution,
            routing.through_proxy(url, self.cfg),
            decision,
            routing.header_lines(routing.headers_for(url, self.cfg)),
        )
        if decision == duplicates.OVERWRITE and target is not None:
            self.run_worker(self._replace(url, options, target))
            return
        self.client.add_uri([url], options)

    async def _replace(self, url: str, options: dict, target: Path) -> None:
        """Clear the old download out before the replacement starts, so aria2
        cannot resurrect its control file on top of the new one."""
        gid = next(
            (
                item.get("gid", "")
                for item in self._in_flight()
                if duplicates.path_of(item) == target
            ),
            "",
        )
        if gid:
            try:
                self.client.remove(gid)
            except (Aria2Error, Aria2Unreachable):
                pass
            await self._settle_then_unlink(gid, target)
        else:
            self._unlink(target)
        self.client.add_uri([url], options)

    def action_limit(self) -> None:
        row = self._selected()
        if row is None:
            return
        if is_youtube_row(row):
            self.notify("speed limits reach aria2 only — a YouTube job has none to change")
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
        if not path.name:
            return
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

    def _delete_youtube(self, row) -> None:
        job_file = STATE_DIR / "yt" / f"{row.gid}.json"
        has_file = bool(row.path.name) and row.path.is_file()

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            for job in ytjob.list_jobs(STATE_DIR / "yt"):
                if job.get("id") != row.gid:
                    continue
                if ytjob.running(job.get("pid", 0)):
                    ytjob.stop(job)
                # Fragments sit outside the destination folder, so deleting the
                # record is the last chance anything has to notice them.
                ytjob.clean_scratch(STATE_DIR / "yt", job)
            job_file.unlink(missing_ok=True)
            job_file.with_suffix(".log").unlink(missing_ok=True)
            if choice == "disk" and has_file:
                self._unlink(row.path)
            self.notify(f"removed {row.name or row.gid}")

        self.push_screen(DeleteModal(row.name or row.gid, has_file), chosen)

    def _delete_active(self) -> None:
        row = self._selected()
        if row is None:
            return
        if is_youtube_row(row):
            self._delete_youtube(row)
            return
        has_file = bool(row.path.name) and row.path.exists()

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            try:
                self.client.remove(row.gid)
            except (Aria2Error, Aria2Unreachable):
                pass
            if choice == "disk" and row.path.name:
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
