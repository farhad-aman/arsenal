import asyncio
from pathlib import Path

from .. import duplicates, history, routing, ytjob, ytrun
from ..config import STATE_DIR, Config
from ..format import human_bytes
from .modals import ConfirmModal, DuplicateModal
from .picker import CancelAll, PickerScreen
from .ytoptions import YouTubeOptionsScreen


def label_for(url: str) -> str:
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    return tail.split("?v=")[-1][:60] or url


class YouTubeAdder:
    """Drives one YouTube download from question to spawned job.

    Options, then destination, then a probe to find out what file it would
    write, then a collision check. Lives apart from any one App so the
    dashboard and the command line ask the same questions in the same order.

    The host supplies push_screen and run_worker — that is the whole contract.
    """

    def __init__(
        self,
        host,
        cfg: Config,
        urls: list[str],
        proxy: bool = False,
        state: Path | None = None,
        spawn=None,
    ):
        self.host = host
        self.cfg = cfg
        self.urls = list(urls)
        self.proxy = proxy
        self.state = state or STATE_DIR
        self.queued: list[dict] = []
        self.skipped: list[dict] = []
        self.cancelled = False
        self.can_burn = ytjob.burn_in_available()
        self._spawn = spawn
        self._finished = None

    def start(self, finished=None) -> None:
        self._finished = finished
        self._ask_options(0)

    def _done(self) -> None:
        if self._finished is not None:
            self._finished(self)

    def _ask_options(self, index: int) -> None:
        if index >= len(self.urls):
            self._done()
            return
        url = self.urls[index]

        def chosen(choices):
            if choices is None:
                self._ask_options(index + 1)
                return
            self._ask_where(index, choices)

        self.host.push_screen(
            YouTubeOptionsScreen(label_for(url), can_burn=self.can_burn), chosen
        )

    def _ask_where(self, index: int, choices) -> None:
        url = self.urls[index]
        category = self.cfg.categories.get("video") or routing.OTHER
        default_dir = category.dir

        def picked(where):
            if isinstance(where, CancelAll):
                self.cancelled = True
                self._done()
                return
            job = ytjob.new_job(
                url,
                Path(where or default_dir),
                choices,
                self.cfg.proxy if routing.through_proxy(url, self.cfg, self.proxy) else "",
                self.cfg.cookies_from,
            )
            self.host.run_worker(self._settle(index, job), exclusive=False)

        self.host.push_screen(
            PickerScreen(
                filename=label_for(url),
                default_dir=default_dir,
                category=category,
                cfg=self.cfg,
                records=history.tail(self.state / "history.jsonl", 200),
                index=index,
                total=len(self.urls),
                theme=self.host.theme_data,
            ),
            picked,
        )

    async def _settle(self, index: int, job: dict) -> None:
        """Find out what file this would write, and ask before treading on it.

        Matching is on the destination alone: the same video at another
        resolution shares a URL but is not the download already on disk.
        """
        try:
            title, filename, total = await asyncio.to_thread(
                ytrun.probe, job, self.cfg.probe_timeout
            )
        except ytrun.ProbeFailed as exc:
            self._ask_blind(index, job, str(exc))
            return
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

        self.host.push_screen(
            DuplicateModal(target.name, collision, human_bytes(collision.size)), decided
        )

    def _ask_blind(self, index: int, job: dict, reason: str) -> None:
        """Without the probe there is no filename, so there is no way to know
        whether this would land on something. Say so rather than queue it and
        let yt-dlp silently decline to overwrite."""

        def decided(go: bool) -> None:
            if go:
                self._queue(index, job)
                return
            self.skipped.append(job)
            self._ask_options(index + 1)

        self.host.push_screen(
            ConfirmModal(
                f"Could not check for an existing copy — {reason}.\n\n"
                "Download anyway? If the file is already there, yt-dlp will "
                "leave it alone."
            ),
            decided,
        )

    def _queue(self, index: int, job: dict) -> None:
        self._spawn(job, self.state)
        self.queued.append(job)
        self._ask_options(index + 1)
