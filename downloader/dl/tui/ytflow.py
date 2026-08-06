import subprocess
import sys
from pathlib import Path

from textual.app import App
from textual.widgets import Static

from .. import instance, ytjob, ytqueue, ytrun
from ..config import STATE_DIR, Config
from ..format import human_bytes
from ..theme import glyph, select
from .table import DownloadTable, row_from_job
from .ytadd import YouTubeAdder, label_for

JOB_DIR = "yt"


def jobs_dir(state: Path = None) -> Path:
    return (state or STATE_DIR) / JOB_DIR


def spawn(job: dict, state: Path = None, cap: int = 0) -> bool:
    """Start this job, or leave it queued behind the ones already running.

    Returns whether it started. A cap of 0 means start it regardless, which is
    what a retry or a resume wants: you asked for that one, now.
    """
    where = state or STATE_DIR
    if cap:
        return ytqueue.launch(job, where, cap)
    ytqueue.hold_slot(where / "yt", job["id"])
    ytqueue.spawn(job, where)
    return True


def resume(job: dict, state: Path = None) -> None:
    """Pick a paused job back up. Fragments live in the scratch directory, which
    a pause leaves alone, so yt-dlp continues rather than starting over."""
    job.update(status="queued", speed=0, error="")
    spawn(job, state)


class YouTubeSetupApp(App):
    """Asks what to download, then where to put it, one video at a time."""

    CSS = """
    Screen { align: center middle; }
    YouTubeOptionsScreen, PickerScreen, DuplicateModal, ConfirmModal,
    PlaylistScreen { align: center middle; }
    #yt-box, #picker-box, #duplicate-box, #confirm-box, #playlist-box {
        width: 76; padding: 1 2; border: round $accent; background: $surface;
    }
    #confirm-box Button { width: 100%; margin-top: 1; }
    #yt-head, #duplicate-head, #playlist-head { text-style: bold; }
    #yt-list, #picker-list, #picker-error, #duplicate-detail, #playlist-detail { height: auto; }
    #duplicate-box Button, #playlist-box Button { width: 100%; margin-top: 1; }
    """

    def __init__(self, cfg: Config, urls: list[str], proxy: bool = False):
        super().__init__()
        self.cfg = cfg
        self.urls = urls
        self.proxy = proxy
        self.theme_data = select(cfg)
        self.queued: list[dict] = []
        self.skipped: list[dict] = []
        self.cancelled = False
        self.failed = ""
        self.adder = YouTubeAdder(self, cfg, urls, proxy, spawn=spawn)

    def on_mount(self) -> None:
        self.adder.start(self._finished)

    def _finished(self, adder: YouTubeAdder) -> None:
        self.queued = adder.queued
        self.skipped = adder.skipped
        self.cancelled = adder.cancelled
        self.failed = adder.failed
        self.exit()


CHECKING = "  asking YouTube what this is…"
SETTLED = ("complete", "error", "cancelled")
WATCH_HINT = "^C detach — the download keeps going    d delete in `dl`"


class YouTubeWatchApp(App):
    """Live view of the jobs just queued, scoped to them alone.

    Exists so a failure is seen in the shell that started it rather than
    discovered later, or never.
    """

    CSS = """
    Screen { layout: vertical; }
    #yt-watch { height: 1fr; padding: 0 1; }
    #yt-watch-hint { dock: bottom; height: 1; padding: 0 1; }
    """

    BINDINGS = [("q", "quit", "quit")]

    def __init__(self, cfg: Config, ids: list[str]):
        super().__init__()
        self.cfg = cfg
        self.ids = set(ids)
        self.theme_data = select(cfg)
        self.table = DownloadTable(self.theme_data, id="yt-watch")
        self.finished: list[dict] = []

    def compose(self):
        yield self.table
        yield Static(WATCH_HINT, id="yt-watch-hint")

    def on_mount(self) -> None:
        self.set_interval(0.5, self.poll)
        self.set_interval(0.1, self.table.refresh_view)
        self.call_after_refresh(self.poll)

    def poll(self) -> None:
        mine = [
            ytjob.reap(jobs_dir(), j)
            for j in ytjob.list_jobs(jobs_dir())
            if j.get("id") in self.ids
        ]
        self.table.set_rows([row_from_job(job, self.cfg) for job in mine])
        if mine and all(job.get("status") in SETTLED for job in mine):
            self.finished = mine
            self.exit()


def watch(cfg: Config, jobs: list[dict]) -> list[str]:
    app = YouTubeWatchApp(cfg, [job["id"] for job in jobs])
    app.run()
    return summarise(app.finished or jobs, select(cfg).icons)


def summarise(jobs: list[dict], icons: bool = True) -> list[str]:
    lines = []
    for job in jobs:
        name = Path(job.get("file") or "").name or job.get("url", "")
        if job.get("status") == "complete":
            mark = glyph("✅", icons)
            lines.append(f"  {mark} {name}   {human_bytes(int(job.get('done', 0) or 0))}")
        elif job.get("status") == "error":
            lines.append(f"  {glyph('❌', icons)} {name}   {job.get('error', 'failed')}")
        else:
            lines.append(f"  {glyph('⏳', icons)} {name}   still downloading — `dl` to watch")
    return lines


_label = label_for


def run_youtube(
    cfg: Config, urls: list[str], proxy: bool = False, state: Path = None
) -> tuple[list[str], bool]:
    """Ask what to download, then watch it.

    Both are full screens, so neither may open beside the dashboard. Unlike a
    direct download there is nothing queued yet — the quality question has not
    been answered — so this refuses rather than standing down, and points at
    the window that can do the same job.
    """
    where = state or STATE_DIR
    if instance.holder(where):
        return ["  dl is already running — press a in that window to add it"], True
    if not instance.acquire(where):
        return ["  dl is already running"], True
    try:
        return _run_youtube(cfg, urls, proxy)
    finally:
        instance.release(where)


def _run_youtube(cfg: Config, urls: list[str], proxy: bool) -> tuple[list[str], bool]:
    app = YouTubeSetupApp(cfg, urls, proxy)
    app.run()
    if app.failed:
        return [f"  {glyph('❌', select(cfg).icons)} {app.failed}"], True
    if app.cancelled:
        return ["  ✖ cancelled — nothing queued"], True
    mark = glyph("⏭", select(cfg).icons)
    skipped = [
        f"  {mark}  skipped  {Path(j['url']).name or j['url']}  — already there"
        for j in app.skipped
    ]
    if not app.queued:
        return skipped, False
    return skipped + watch(cfg, app.queued), False
