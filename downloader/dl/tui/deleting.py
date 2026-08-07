from pathlib import Path

from .. import cli, history, ytjob
from ..rpc import Aria2Error, Aria2Unreachable
from .completed import record_path
from .modals import DeleteModal
from .queueing import settle_then_unlink, unlink_download
from .table import is_youtube_row

DISK = "disk"


class Deleting:
    """Removes a download, and its file if that is what was asked for.

    Takes what to delete rather than looking at the selection: the App owns
    which row the cursor is on, this owns what removing one means. The host
    supplies push_screen, run_worker and notify.
    """

    def __init__(self, host, client, state: Path, history_log: Path):
        self.host = host
        self.client = client
        self.state = state
        self.history_log = history_log

    @property
    def jobs(self) -> Path:
        return self.state / "yt"

    def delete_row(self, row) -> None:
        if is_youtube_row(row):
            self._delete_youtube(row)
        else:
            self._delete_active(row)

    def _delete_youtube(self, row) -> None:
        job_file = self.jobs / f"{row.gid}.json"
        has_file = bool(row.path.name) and row.path.is_file()

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            for job in ytjob.list_jobs(self.jobs):
                if job.get("id") != row.gid:
                    continue
                if ytjob.running(job.get("pid", 0)):
                    ytjob.stop(job)
                # Fragments sit outside the destination folder, so deleting the
                # record is the last chance anything has to notice them.
                ytjob.clean_scratch(self.jobs, job)
            job_file.unlink(missing_ok=True)
            job_file.with_suffix(".log").unlink(missing_ok=True)
            if choice == DISK and has_file:
                unlink_download(row.path)
            self.host.notify(f"removed {row.name or row.gid}")

        self.host.push_screen(DeleteModal(row.name or row.gid, has_file), chosen)

    def _delete_active(self, row) -> None:
        has_file = bool(row.path.name) and row.path.exists()

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            try:
                self.client.remove(row.gid)
            except (Aria2Error, Aria2Unreachable):
                pass
            if choice == DISK and row.path.name:
                self.host.run_worker(settle_then_unlink(self.client, row.gid, row.path))
                self.host.notify(f"deleted {row.name}")
            else:
                # Deleting the file waits for aria2 to let go of it, and the
                # result cannot be forgotten until then.
                cli.forget_result(self.client, row.gid)

        self.host.push_screen(DeleteModal(row.name or row.gid, has_file), chosen)

    def delete_record(self, record: dict, on_removed=None) -> None:
        """A finished download: the history line, and optionally the file."""
        path = record_path(record)
        has_file = bool(path and path.exists())

        def chosen(choice: str | None) -> None:
            if choice is None:
                return
            history.remove_entry(self.history_log, record)
            if choice == DISK and path:
                unlink_download(path)
            if on_removed is not None:
                on_removed()
            self.host.notify(
                f"removed {record.get('name', '')}"
                + (" and its file" if choice == DISK else " from the list")
            )

        self.host.push_screen(
            DeleteModal(record.get("name", "") or "entry", has_file), chosen
        )
