import asyncio
import time
from pathlib import Path

from .. import cli, duplicates, history, routing, torrent
from ..config import Config
from ..destinations import ensure_writable
from ..format import human_bytes
from ..rpc import Aria2Error, Aria2Unreachable
from .modals import DuplicateModal

SETTLED = ("removed", "error", "complete")
SETTLE_TIMEOUT = 5.0


def unlink_download(path: Path) -> None:
    """Remove a download and the control file aria2 keeps beside it."""
    if not path.name:
        return
    for target in (path, path.with_name(path.name + ".aria2")):
        try:
            target.unlink()
        except OSError:
            pass


async def settle_then_unlink(client, gid: str, path: Path) -> None:
    """aria2 rewrites the control file while winding a download down, so a
    sidecar deleted the instant remove() returns comes straight back."""
    deadline = time.monotonic() + SETTLE_TIMEOUT
    while time.monotonic() < deadline:
        try:
            status = client.tell_status(gid).get("status", "")
        except (Aria2Error, Aria2Unreachable):
            break
        if status in SETTLED:
            cli.forget_result(client, gid)
            break
        await asyncio.sleep(0.05)
    unlink_download(path)


def in_flight(client) -> list[dict]:
    try:
        return list(client.tell_active()) + list(client.tell_waiting())
    except (Aria2Error, Aria2Unreachable):
        return []


class Queueing:
    """Turns URLs into aria2 downloads, asking about collisions on the way.

    The host supplies push_screen, run_worker, notify and rpc — the same
    contract YouTubeAdder works to. Living apart from the App is what lets the
    duplicate-and-overwrite path be driven without starting a screen.
    """

    def __init__(self, host, cfg: Config, client, history_log: Path):
        self.host = host
        self.cfg = cfg
        self.client = client
        self.history_log = history_log

    def add(self, urls: list[str], after=None) -> None:
        self._next(list(urls), 0, after)

    def _next(self, urls: list[str], index: int, after=None) -> None:
        """One URL at a time, so a collision can be asked about before the next
        one is considered."""
        if index >= len(urls):
            if after is not None:
                after()
            return
        url = urls[index]
        name = routing.filename_from_url(url)
        resolution = routing.resolve(url, name, self.cfg)
        target = resolution.path / name if name else None
        # A torrent is a hash until the swarm says what it holds, so there
        # is no name yet to ask about a collision with.
        collision = (
            None
            if torrent.is_torrent(url)
            else duplicates.detect(
                url, target, history.tail(self.history_log, 200), in_flight(self.client)
            )
        )
        if collision is None:
            self.queue_one(url, resolution, None, target)
            self._next(urls, index + 1, after)
            return

        def decided(choice: str | None) -> None:
            """Escape declines this one and moves on — the dashboard has no
            batch to abandon."""
            if choice is not None:
                self.queue_one(url, resolution, choice, target)
            self._next(urls, index + 1, after)

        self.host.push_screen(
            DuplicateModal(name or url, collision, human_bytes(collision.size)), decided
        )

    def queue_one(
        self, url: str, resolution, decision: str | None, target: Path | None
    ) -> None:
        if decision == duplicates.SKIP:
            self.host.notify(f"skipped {resolution.path.name or url}")
            return
        if not ensure_writable(resolution.path):
            # An unmounted drive or a read-only volume, which the picker and
            # the command line both check for and the dashboard did not.
            self.host.notify(f"cannot write to {resolution.path}", severity="error")
            return
        options = cli.add_options(
            self.cfg,
            resolution,
            routing.through_proxy(url, self.cfg),
            decision,
            routing.header_lines(routing.headers_for(url, self.cfg)),
        )
        if decision == duplicates.OVERWRITE and target is not None:
            self.host.run_worker(self._replace(url, options, target))
            return
        if torrent.is_torrent_file(url):
            self.host.rpc(
                f"could not queue {Path(url).name}",
                lambda: self.client.add_torrent(Path(url).expanduser(), options),
            )
            return
        self.host.rpc(
            f"could not queue {resolution.path.name or url}",
            lambda: self.client.add_uri([url], options),
        )

    async def _replace(self, url: str, options: dict, target: Path) -> None:
        """Clear the old download out before the replacement starts, so aria2
        cannot resurrect its control file on top of the new one."""
        gid = next(
            (
                item.get("gid", "")
                for item in in_flight(self.client)
                if duplicates.path_of(item) == target
            ),
            "",
        )
        if gid:
            try:
                self.client.remove(gid)
            except (Aria2Error, Aria2Unreachable):
                pass
            await settle_then_unlink(self.client, gid, target)
        else:
            unlink_download(target)
        self.host.rpc(
            f"could not queue {target.name}", lambda: self.client.add_uri([url], options)
        )
