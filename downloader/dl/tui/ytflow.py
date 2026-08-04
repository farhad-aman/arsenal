import subprocess
import sys
from pathlib import Path

from textual.app import App

from .. import history, routing, ytjob
from ..config import STATE_DIR, Config
from ..theme import select
from .picker import CancelAll, PickerScreen
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


class YouTubeSetupApp(App):
    """Asks what to download, then where to put it, one video at a time."""

    CSS = """
    Screen { align: center middle; }
    YouTubeOptionsScreen, PickerScreen { align: center middle; }
    #yt-box, #picker-box {
        width: 76; padding: 1 2; border: round $accent; background: $surface;
    }
    #yt-head { text-style: bold; }
    #yt-list, #picker-list, #picker-error { height: auto; }
    """

    def __init__(self, cfg: Config, urls: list[str], proxy: bool = False):
        super().__init__()
        self.cfg = cfg
        self.urls = urls
        self.proxy = proxy
        self.theme_data = select(cfg)
        self.queued: list[dict] = []
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
            target = Path(where or default_dir)
            job = ytjob.new_job(
                url, target, choices, self.cfg.proxy if self.proxy else ""
            )
            spawn(job)
            self.queued.append(job)
            self._ask_options(index + 1)

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


def _label(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("?v=")[-1][:60] or url


def run_youtube(cfg: Config, urls: list[str], proxy: bool = False) -> tuple[list[str], bool]:
    app = YouTubeSetupApp(cfg, urls, proxy)
    app.run()
    if app.cancelled:
        return ["  ✖ cancelled — nothing queued"], True
    lines = [f"  ▶ queued  {job['url']}  →  {job['dir']}" for job in app.queued]
    if lines:
        lines.append("  run `dl` to watch them")
    return lines, False
