import json
import os
from pathlib import Path

from . import search

_BLOCK = 8192


def append(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def key(record: dict) -> tuple:
    """What makes two history lines the same download.

    The same file fetched twice is two records, so the name alone will not do.
    """
    return (record.get("ts"), record.get("path"), record.get("name"))


def remove_entry(path: Path, record: dict) -> bool:
    if not path.exists():
        return False
    target = key(record)
    kept: list[str] = []
    removed = False
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not removed:
            try:
                if key(json.loads(raw)) == target:
                    removed = True
                    continue
            except json.JSONDecodeError:
                pass
        kept.append(raw)
    if removed:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text("".join(line + "\n" for line in kept), encoding="utf-8")
        temp.replace(path)
    return removed


def find(path: Path, query: str, n: int) -> list[dict]:
    """Matching records from the whole log, newest n kept, oldest first.

    tail() reads backwards from the end and stops, so it cannot answer a search
    — a name older than the last n records would never be seen.
    """
    if n <= 0 or not path.exists():
        return []
    if not search.active(query):
        return tail(path, n)

    found: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict) and search.matches(parsed.get("name") or "", query):
                found.append(parsed)
    return found[-n:]


def tail(path: Path, n: int) -> list[dict]:
    if n <= 0 or not path.exists():
        return []
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        chunks: list[bytes] = []
        read = 0
        while read < size and sum(c.count(b"\n") for c in chunks) <= n:
            step = min(_BLOCK, size - read)
            read += step
            fh.seek(size - read)
            chunks.insert(0, fh.read(step))
        blob = b"".join(chunks)

    records: list[dict] = []
    for raw in blob.decode("utf-8", errors="replace").splitlines():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records[-n:]
