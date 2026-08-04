from dataclasses import dataclass
from pathlib import Path

BOTH = "both"
URL_ONLY = "url"
PATH_ONLY = "path"

SKIP = "skip"
RENAME = "rename"
OVERWRITE = "overwrite"
DOWNLOAD = "download"

_CHOICES = {
    BOTH: (SKIP, RENAME, OVERWRITE),
    URL_ONLY: (SKIP, DOWNLOAD),
    PATH_ONLY: (SKIP, RENAME, OVERWRITE),
}


@dataclass(frozen=True)
class Collision:
    kind: str
    path: Path
    url: str
    gid: str = ""
    status: str = ""
    size: int = 0

    @property
    def in_flight(self) -> bool:
        return bool(self.gid)

    @property
    def risky_overwrite(self) -> bool:
        """The name matches but the source does not, so overwriting destroys
        something that was never this download."""
        return self.kind == PATH_ONLY

    @property
    def choices(self) -> tuple[str, ...]:
        return choices_for(self.kind)


def choices_for(kind: str) -> tuple[str, ...]:
    return _CHOICES[kind]


def _file(item: dict) -> dict:
    return (item.get("files") or [{}])[0]


def path_of(item: dict) -> Path:
    """The file an aria2 status dict is writing to."""
    return Path(_file(item).get("path", "") or "")


def _url_of(item: dict) -> str:
    uris = _file(item).get("uris") or []
    return uris[0].get("uri", "") if uris else ""


def _kind_for(existing_url: str, url: str) -> str:
    return BOTH if existing_url == url else PATH_ONLY


def detect(
    url: str, target: Path | None, records: list[dict], downloads: list[dict]
) -> Collision | None:
    """Decide whether queuing `url` into `target` collides with something.

    A download still running at that path outranks anything in history: it is
    the one that would actually be clobbered. `target` is None when the server
    has not named the file yet, which leaves only the URL to go on.
    """
    for item in downloads:
        if target is not None and path_of(item) == target:
            existing = _url_of(item)
            return Collision(
                kind=_kind_for(existing, url),
                path=target,
                url=existing,
                gid=item.get("gid", ""),
                status=item.get("status", ""),
                size=int(item.get("totalLength", 0) or 0),
            )

    if target is not None and target.exists():
        prior = _latest_record_for(records, target)
        existing = prior.get("url", "") if prior else ""
        return Collision(
            kind=_kind_for(existing, url),
            path=target,
            url=existing,
            size=target.stat().st_size,
        )

    for item in downloads:
        if _url_of(item) == url:
            return Collision(
                kind=URL_ONLY,
                path=path_of(item),
                url=url,
                gid=item.get("gid", ""),
                status=item.get("status", ""),
                size=int(item.get("totalLength", 0) or 0),
            )

    for record in reversed(records):
        if record.get("url") != url or record.get("status") != "ok":
            continue
        where = Path(record.get("path", "") or "")
        if where.name and where.exists():
            return Collision(
                kind=URL_ONLY,
                path=where,
                url=url,
                size=int(record.get("bytes", 0) or 0),
            )
    return None


def _latest_record_for(records: list[dict], target: Path) -> dict | None:
    for record in reversed(records):
        if record.get("path") == str(target):
            return record
    return None
