import time
from dataclasses import dataclass
from pathlib import Path

from .. import history
from ..config import Category
from ..format import human_bytes, human_duration, human_speed
from ..theme import select
from .app import DlApp
from .picker import PickerScreen

RUNNING = ("active", "waiting")


@dataclass(frozen=True)
class Request:
    url: str
    filename: str
    default_dir: Path
    category: Category
PREVIEW_HINT = (
    "space pause/resume   l limit   L limit this   o open   f finder   "
    "d delete   ^C detach"
)
MARKS = {
    True: {"ok": "✅", "fail": "❌", "wait": "⏳"},
    False: {"ok": "[ok]", "fail": "[fail]", "wait": "[...]"},
}


def summarise(results: list[dict], icons: bool = True) -> list[str]:
    """Format finished download results for printing after the preview exits."""
    mark = MARKS[bool(icons)]
    lines: list[str] = []
    running = 0
    for item in results:
        status = item.get("status", "")
        if status in RUNNING:
            running += 1
            continue
        name = item.get("name") or "(unnamed)"
        if status == "complete":
            total = int(item.get("bytes", 0) or 0)
            seconds = int(item.get("seconds", 0) or 0)
            detail = human_bytes(total)
            if seconds > 0:
                detail = (
                    f"{detail} in {human_duration(seconds)}   avg {human_speed(total // seconds)}"
                )
            lines.append(f"  {mark['ok']} {name}   {detail}")
        elif status == "removed":
            lines.append(f"  {mark['fail']} {name}   removed")
        else:
            lines.append(f"  {mark['fail']} {name}   {item.get('error') or 'failed'}")
    if running:
        lines.append(
            f"  {mark['wait']} {running} still downloading — `dl` to watch, `dl ls` to list"
        )
    return lines


class PreviewApp(DlApp):
    """Dashboard scoped to a set of gids, exiting once they all settle.

    Textual merges BINDINGS across the MRO, so keys inherited from DlApp cannot
    be removed by redeclaring a shorter list — they are disabled by overriding
    their action methods instead.
    """

    splash_when_empty = False

    def __init__(self, cfg, client, gids=(), pending=(), queue=None):
        super().__init__(cfg, client)
        self.watch = set(gids)
        self.results: list[dict] = []
        self.hint_text = PREVIEW_HINT
        self.pending = list(pending)
        self.queue = queue
        self.picking = bool(self.pending)
        self.chosen: list[Path | None] = []

    def on_mount(self) -> None:
        super().on_mount()
        self.hint.update(PREVIEW_HINT)
        if self.pending:
            self._ask(0)

    def _ask(self, index: int) -> None:
        if index >= len(self.pending):
            self._finish_picking()
            return
        item = self.pending[index]

        def chosen(value):
            self.chosen.append(value)
            self._ask(index + 1)

        self.push_screen(
            PickerScreen(
                filename=item.filename,
                default_dir=item.default_dir,
                category=item.category,
                cfg=self.cfg,
                records=history.tail(self.history_log, 200),
                index=index,
                total=len(self.pending),
                theme=self.theme_data,
            ),
            chosen,
        )

    def _finish_picking(self) -> None:
        self.picking = False
        gids = self.queue(self.chosen) if self.queue else []
        self.watch = set(gids)
        if not self.watch:
            self.exit()

    def action_add(self) -> None:
        return None

    def action_toggle_tab(self) -> None:
        return None

    def action_move_down(self) -> None:
        return None

    def action_move_up(self) -> None:
        return None

    def action_retry(self) -> None:
        return None

    def _filter_items(self, items: list[dict]) -> list[dict]:
        return [item for item in items if item.get("gid") in self.watch]

    def _after_refresh(self, items: list[dict]) -> None:
        if self.picking or items:
            return
        self.results = self._collect_results()
        self.exit()

    def _collect_results(self) -> list[dict]:
        collected = []
        elapsed = max(int(time.monotonic() - self.started), 0)
        for gid in self.watch:
            try:
                raw = self.client.tell_status(gid)
            except Exception:
                continue
            files = raw.get("files") or [{}]
            collected.append(
                {
                    "name": Path(files[0].get("path", "") or "").name or gid,
                    "status": raw.get("status", ""),
                    "bytes": int(raw.get("completedLength", 0) or 0),
                    "seconds": elapsed,
                    "error": raw.get("errorMessage", "") or "",
                }
            )
        return sorted(collected, key=lambda r: r["name"])


def run_preview(cfg, client, gids=(), pending=(), queue=None) -> list[str]:
    app = PreviewApp(cfg, client, gids, pending, queue)
    app.run()
    return summarise(app.results, icons=select(cfg).icons)
