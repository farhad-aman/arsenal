import base64
from pathlib import Path

from .rpc import Aria2Error, Aria2Unreachable

MAGNET = "magnet:"
SUFFIX = ".torrent"

# aria2 answers a magnet with a placeholder download whose only job is to fetch
# the torrent file. It completes in seconds and hands off to a second gid.
METADATA_PREFIX = "[METADATA]"


def is_magnet(value: str) -> bool:
    return value.strip().lower().startswith(MAGNET)


def is_torrent_file(value: str) -> bool:
    """A .torrent on disk, as opposed to one to be fetched over HTTP.

    A remote .torrent needs no special handling: aria2 downloads it and follows
    it into the real transfer on its own.
    """
    if not value.lower().endswith(SUFFIX):
        return False
    try:
        return Path(value).expanduser().is_file()
    except OSError:
        return False


def is_torrent(value: str) -> bool:
    return is_magnet(value) or is_torrent_file(value)


def encoded(path: Path) -> str:
    """A .torrent as aria2's addTorrent wants it."""
    return base64.b64encode(path.expanduser().read_bytes()).decode()


def name_of(status: dict) -> str:
    """What to call a torrent.

    A multi-file torrent is a folder of parts, so the first file's name is a
    fragment of the thing rather than its name. The torrent says what it is.
    """
    info = (status.get("bittorrent") or {}).get("info") or {}
    return str(info.get("name") or "")


def is_metadata(status: dict) -> bool:
    """A magnet's placeholder rather than a download anyone asked for."""
    if status.get("followedBy"):
        return True
    return name_of(status).startswith(METADATA_PREFIX) or _first_name(status).startswith(
        METADATA_PREFIX
    )


def _first_name(status: dict) -> str:
    files = status.get("files") or [{}]
    return Path(files[0].get("path", "") or "").name


def is_torrent_status(status: dict) -> bool:
    return bool(status.get("bittorrent"))


def is_multi_file(status: dict) -> bool:
    return (status.get("bittorrent") or {}).get("mode") == "multi"


def target(status: dict, directory: Path) -> Path:
    """Where this torrent lands.

    One file lands as that file; several land as a folder named for the
    torrent, which is what aria2 writes and what deleting has to remove.
    """
    name = name_of(status)
    if is_multi_file(status):
        return directory / name if name else directory
    files = status.get("files") or []
    first = Path(files[0].get("path", "") or "") if files else Path("")
    return first if first.name else directory / name


def source_of(client, status: dict) -> Path | None:
    """The .torrent that was fetched in order to start this download.

    Asking for one over http downloads it into the destination, where it then
    sits beside the thing it described. A magnet's parent is a [METADATA]
    placeholder with no file behind it, which is why the suffix is checked
    rather than assumed.
    """
    parent = status.get("following") or ""
    if not parent:
        return None
    try:
        origin = client.tell_status(parent)
    except (Aria2Error, Aria2Unreachable):
        return None
    files = origin.get("files") or [{}]
    raw = files[0].get("path", "") or ""
    return Path(raw) if raw.lower().endswith(SUFFIX) else None
