import json
import os
from pathlib import Path

_BLOCK = 8192


def append(record: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False) + "\n"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


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
