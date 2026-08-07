import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

LOG = "started.jsonl"
LIMIT = 500


def _path(state: Path) -> Path:
    return Path(state) / LOG


def record(state: Path, gid: str, when: int | None = None) -> None:
    """Note when a download joined the queue.

    aria2 does not remember this: its status has no queued-at field, so the
    moment the gid is handed back is the only chance to learn it. Appended
    rather than rewritten so the dashboard, `dl` and `dl watch` can all be
    adding downloads at once without clobbering each other.
    """
    if not gid:
        return
    path = _path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"gid": gid, "ts": int(time.time() if when is None else when)}) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


def load(state: Path) -> dict[str, int]:
    path = _path(state)
    if not path.exists():
        return {}
    found: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        gid = entry.get("gid") or ""
        # A repeat line is a retry of the same gid; the first is when it began.
        if gid and gid not in found:
            found[gid] = int(entry.get("ts", 0) or 0)
    return found


def when(state: Path, gid: str) -> int:
    return load(state).get(gid, 0)


def overgrown(entries: dict[str, int]) -> bool:
    return len(entries) > LIMIT


def prune(state: Path, live: Iterable[str]) -> None:
    """Drop gids aria2 no longer knows about.

    Finished downloads carry their own timestamp into the history log, so
    nothing is lost by forgetting them here.
    """
    path = _path(state)
    if not path.exists():
        return
    keep = set(live)
    kept = {gid: ts for gid, ts in load(state).items() if gid in keep}
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        "".join(json.dumps({"gid": gid, "ts": ts}) + "\n" for gid, ts in kept.items()),
        encoding="utf-8",
    )
    os.replace(temp, path)
