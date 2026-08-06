import subprocess
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .youtube import is_youtube

SEPARATOR = "\t"
# The collection's own name comes back on every line, which is the only way a
# flat listing offers it. Any line will do.
FORMAT = f"%(url)s{SEPARATOR}%(title)s{SEPARATOR}%(playlist_title,channel,uploader)s"

# A channel, however it is spelled. /videos, /shorts and /streams are tabs of
# the same thing and expand the same way.
_CHANNEL_PREFIXES = ("/channel/", "/c/", "/user/", "/@")


@dataclass(frozen=True)
class Entry:
    url: str
    title: str
    collection: str = ""


def is_collection(url: str) -> bool:
    """Whether this address means "many videos" rather than "this one".

    A watch link carries the playlist it was opened from, so `list=` alone
    cannot decide it: copying the address of something you are watching would
    otherwise queue the whole playlist behind it.
    """
    if not is_youtube(url):
        return False
    try:
        parts = urlparse(url)
    except ValueError:
        return False
    path = parts.path.rstrip("/")
    if path.startswith("/watch") or path.startswith("/shorts/"):
        return False
    if parts.netloc.endswith("youtu.be"):
        return False
    if path.startswith("/playlist") and parse_qs(parts.query).get("list"):
        return True
    return any(path.startswith(prefix) for prefix in _CHANNEL_PREFIXES)


def list_command(url: str, proxy: str, cookies_from: str, limit: int = 0) -> list[str]:
    """Ask yt-dlp what is in there, without extracting each video.

    Flat means one request for the whole listing rather than one per entry,
    which is the difference between a moment and several minutes.
    """
    argv = ["yt-dlp", "--flat-playlist", "--no-warnings", "--ignore-errors"]
    if proxy:
        argv += ["--proxy", proxy]
    if cookies_from:
        argv += ["--cookies-from-browser", cookies_from]
    if limit:
        argv += ["--playlist-end", str(limit)]
    argv += ["--print", FORMAT, url]
    return argv


class ListingFailed(Exception):
    pass


def expand(url: str, proxy: str, cookies_from: str, limit: int = 0, timeout: float = 120):
    """The videos in a collection, in the order YouTube lists them."""
    try:
        done = subprocess.run(
            list_command(url, proxy, cookies_from, limit),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise ListingFailed(f"timed out after {int(timeout)}s") from None
    except OSError as exc:
        raise ListingFailed(str(exc)) from None
    entries = parse_entries(done.stdout)
    if not entries:
        detail = (done.stderr or "").strip().splitlines()
        raise ListingFailed(detail[-1] if detail else "nothing in it")
    return entries


def parse_entries(output: str) -> list[Entry]:
    entries = []
    for line in output.splitlines():
        if SEPARATOR not in line:
            continue
        url, _, rest = line.partition(SEPARATOR)
        url = url.strip()
        if not url.startswith(("http://", "https://")):
            continue
        title, _, collection = rest.rpartition(SEPARATOR)
        if not title:
            title, collection = collection, ""
        entries.append(Entry(url, title.strip(), collection.strip()))
    return entries


def name_of(entries: list[Entry], fallback: str = "") -> str:
    """What to call this collection. NA is yt-dlp's way of saying it has none."""
    for entry in entries:
        if entry.collection and entry.collection != "NA":
            return entry.collection
    return fallback
