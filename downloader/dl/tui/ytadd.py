import asyncio
from pathlib import Path

from .. import duplicates, history, playlist, routing, ytjob, ytrun
from ..config import STATE_DIR, Config
from ..format import human_bytes
from .modals import ConfirmModal, DuplicateModal
from .picker import CancelAll, PickerScreen
from .playlistscreen import PlaylistScreen
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
        titles: dict[str, str] | None = None,
        shared: bool = False,
    ):
        self.host = host
        self.cfg = cfg
        self.urls = list(urls)
        # A collection is one decision, not one per video. The titles come
        # from the flat listing, which is why these need no probe.
        self.shared = shared
        self.titles = dict(titles or {})
        self._choices = None
        self._where = None
        self.proxy = proxy
        self.state = state or STATE_DIR
        self.queued: list[dict] = []
        self.skipped: list[dict] = []
        self.cancelled = False
        self.failed = ""
        self.can_burn = ytjob.burn_in_available()
        self._spawn = spawn
        self._finished = None

    def start(self, finished=None) -> None:
        self._finished = finished
        if not ytjob.ffmpeg_available():
            # Before anything is fetched. Without it yt-dlp downloads the
            # streams and fails at the last step, leaving a .webm where the
            # file you asked for should be.
            self.failed = ytjob.FFMPEG_ADVICE
            self._done()
            return
        collections = [url for url in self.urls if playlist.is_collection(url)]
        if collections and not self.shared:
            self.urls = [url for url in self.urls if url not in collections]
            self.host.run_worker(self._open_collection(collections[0]), exclusive=False)
            return
        self._ask_options(0)

    async def _open_collection(self, url: str) -> None:
        """A playlist or channel: find out what is in it, then ask how much.

        Listing is flat — one request for the whole thing rather than one per
        video — so this is a moment even for a long channel.
        """
        try:
            entries = await asyncio.to_thread(
                playlist.expand, url, self._proxy_for(url), self.cfg.cookies_from
            )
        except playlist.ListingFailed as exc:
            self.failed = f"{url}: {exc}"
            self._done()
            return

        def decided(count: int | None) -> None:
            if not count:
                self.cancelled = True
                self._done()
                return
            taken = entries[:count]
            self.urls = [entry.url for entry in taken]
            self.titles = {entry.url: entry.title for entry in taken}
            self.shared = True
            self._ask_options(0)

        self.host.push_screen(
            PlaylistScreen(self.collection_title(url, entries), len(entries)), decided
        )

    def collection_title(self, url: str, entries) -> str:
        return playlist.name_of(entries, label_for(url))

    def _proxy_for(self, url: str) -> str:
        return self.cfg.proxy if routing.through_proxy(url, self.cfg, self.proxy) else ""

    def _done(self) -> None:
        if self._finished is not None:
            self._finished(self)

    def _ask_options(self, index: int) -> None:
        if index >= len(self.urls):
            self._done()
            return
        if self.shared and self._choices is not None:
            self._queue_shared(index)
            return
        url = self.urls[index]

        def chosen(choices):
            if choices is None:
                self._ask_options(index + 1)
                return
            self._choices = choices
            self._ask_where(index, choices)

        self.host.push_screen(
            YouTubeOptionsScreen(self._label(url), can_burn=self.can_burn), chosen
        )

    def _label(self, url: str) -> str:
        return self.titles.get(url) or label_for(url)

    def _queue_shared(self, index: int) -> None:
        """Every video after the first, on the answers already given.

        No probe: the listing carried the titles, and asking YouTube about each
        video in turn would hold a long playlist at the starting line for
        minutes before anything downloaded.
        """
        url = self.urls[index]
        job = self._job_for(url, self._where)
        job["title"] = self.titles.get(url, "")
        self._queue(index, job)

    def _job_for(self, url: str, where):
        category = self.cfg.categories.get("video") or routing.OTHER
        return ytjob.new_job(
            url,
            Path(where or category.dir),
            self._choices,
            self.cfg.proxy if routing.through_proxy(url, self.cfg, self.proxy) else "",
            self.cfg.cookies_from,
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
            self._where = where
            job = ytjob.new_job(
                url,
                Path(where or default_dir),
                choices,
                self.cfg.proxy if routing.through_proxy(url, self.cfg, self.proxy) else "",
                self.cfg.cookies_from,
            )
            if self.shared:
                job["title"] = self.titles.get(url, "")
                self._queue(index, job)
                return
            self.host.run_worker(self._settle(index, job), exclusive=False)

        self.host.push_screen(
            PickerScreen(
                filename=self._label(url),
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
        # A collection is the reason this cap exists: accepting one used to
        # start every video at the same moment.
        self._spawn(job, self.state, self.cfg.general.max_concurrent if self.shared else 0)
        self.queued.append(job)
        self._ask_options(index + 1)
