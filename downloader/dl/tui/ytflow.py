import subprocess
import sys
from pathlib import Path

import asyncio

from textual.app import App
from textual.widgets import Static

from .. import duplicates, history, routing, ytjob
from ..config import STATE_DIR, Config
from ..format import human_bytes
from ..theme import select
from .modals import DuplicateModal
from .picker import CancelAll, PickerScreen
from .table import DownloadTable, row_from_job
from .ytoptions import YouTubeOptionsScreen

JOB_DIR = "yt"


def jobs_dir(state: Path = None) -> Path:
    return (state or STATE_DIR) / JOB_DIR


def spawn(job: dict, state: Path = None) -> None:
    """Detach the supervisor so closing the shell never stops the download."""
    target = ytjob.save(jobs_dir(state), job)
    subprocess.Popen(
        [sys.executable, "-m", "dl.ytrun", str(target)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def resume(job: dict, state: Path = None) -> None:
    """Pick a paused job back up. Fragments live in the scratch directory, which
    a pause leaves alone, so yt-dlp continues rather than starting over."""
    job.update(status="queued", speed=0, error="")
    spawn(job, state)


class YouTubeSetupApp(App):
    """Asks what to download, then where to put it, one video at a time."""

    CSS = """
    Screen { align: center middle; }
    YouTubeOptionsScreen, PickerScreen, DuplicateModal { align: center middle; }
    #yt-box, #picker-box, #duplicate-box {
        width: 76; padding: 1 2; border: round $accent; background: $surface;
    }
    #yt-head, #duplicate-head { text-style: bold; }
    #yt-list, #picker-list, #picker-error, #duplicate-detail { height: auto; }
    #duplicate-box Button { width: 100%; margin-top: 1; }
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
        self.can_burn = ytjob.burn_in_available()

    def on_mount(self) -> None:
        self._ask_options(0)

    def _ask_options(self, index: int) -> None:
        if index >= len(self.urls):
            self.exit()
            return
        url = self.urls[index]

        def chosen(choices):
            if choices is None:
                self._ask_options(index + 1)
                return
            self._ask_where(index, choices)

        self.push_screen(YouTubeOptionsScreen(_label(url), can_burn=self.can_burn), chosen)

    def _ask_where(self, index: int, choices) -> None:
        url = self.urls[index]
        category = self.cfg.categories.get("video") or routing.OTHER
        default_dir = category.dir

        def picked(where):
            if isinstance(where, CancelAll):
                self.cancelled = True
                self.exit()
                return
            job = ytjob.new_job(
                url,
                Path(where or default_dir),
                choices,
                self.cfg.proxy if self.proxy else "",
                self.cfg.cookies_from,
            )
            self.run_worker(self._settle(index, job), exclusive=False)

        self.push_screen(
            PickerScreen(
                filename=_label(url),
                default_dir=default_dir,
                category=category,
                cfg=self.cfg,
                records=history.tail(STATE_DIR / "history.jsonl", 200),
                index=index,
                total=len(self.urls),
                theme=self.theme_data,
            ),
            picked,
        )

    async def _settle(self, index: int, job: dict) -> None:
        """Find out what file this would write, and ask before treading on it.

        Matching is on the destination alone: the same video at another
        resolution shares a URL but is not the download already on disk.
        """
        title, filename, total = await _probe_job(job)
        job["title"] = title
        job["total"] = total
        target = Path(filename) if filename else None
        collision = duplicates.detect_target(target) if target else None
        if collision is None:
            self._queue(index, job)
            return

        def decided(choice: str | None) -> None:
            if choice is None or choice == duplicates.SKIP:
                self.skipped.append(job)
                self._ask_options(index + 1)
                return
            if choice == duplicates.RENAME:
                job["outname"] = duplicates.free_name(target).name
            elif choice == duplicates.OVERWRITE:
                job["outname"] = target.name
                job["force"] = True
            self._queue(index, job)

        self.push_screen(
            DuplicateModal(target.name, collision, human_bytes(collision.size)), decided
        )

    def _queue(self, index: int, job: dict) -> None:
        spawn(job)
        self.queued.append(job)
        self._ask_options(index + 1)


CHECKING = "  ⏳  asking YouTube what this is…"
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
        mine = [j for j in ytjob.list_jobs(jobs_dir()) if j.get("id") in self.ids]
        self.table.set_rows([row_from_job(job, self.cfg) for job in mine])
        if mine and all(job.get("status") in SETTLED for job in mine):
            self.finished = mine
            self.exit()


def watch(cfg: Config, jobs: list[dict]) -> list[str]:
    app = YouTubeWatchApp(cfg, [job["id"] for job in jobs])
    app.run()
    return summarise(app.finished or jobs)


def summarise(jobs: list[dict]) -> list[str]:
    lines = []
    for job in jobs:
        name = Path(job.get("file") or "").name or job.get("url", "")
        if job.get("status") == "complete":
            lines.append(f"  ✅ {name}   {human_bytes(int(job.get('done', 0) or 0))}")
        elif job.get("status") == "error":
            lines.append(f"  ❌ {name}   {job.get('error', 'failed')}")
        else:
            lines.append(f"  ⏳ {name}   still downloading — `dl` to watch")
    return lines


async def _probe_job(job: dict) -> tuple[str, str, int]:
    from .. import ytrun

    return await asyncio.to_thread(ytrun.probe, job)


def _label(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("?v=")[-1][:60] or url


def run_youtube(cfg: Config, urls: list[str], proxy: bool = False) -> tuple[list[str], bool]:
    app = YouTubeSetupApp(cfg, urls, proxy)
    app.run()
    if app.cancelled:
        return ["  ✖ cancelled — nothing queued"], True
    skipped = [f"  ⏭  skipped  {Path(j['url']).name or j['url']}  — already there" for j in app.skipped]
    if not app.queued:
        return skipped, False
    return skipped + watch(cfg, app.queued), False
