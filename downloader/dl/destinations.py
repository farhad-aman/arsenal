import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import Category, Config

PATHLIKE = ("/", "~", ".")
RECENT_ICON = "🕘"
CWD_ICON = "📁"
CREATE_ICON = "✏️"
DISK_ICON = "📂"


@dataclass(frozen=True)
class Candidate:
    path: Path
    icon: str
    note: str
    kind: str


def ensure_writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(path, os.W_OK)


def recent_destinations(records: list[dict], limit: int = 5) -> list[tuple[Path, int]]:
    counts: Counter = Counter()
    last: dict[Path, int] = {}
    for index, record in enumerate(records):
        raw = record.get("path") or ""
        if not raw:
            continue
        parent = Path(raw).parent
        counts[parent] += 1
        last[parent] = index
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -last[kv[0]]))
    return ranked[:limit]


def _extension(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def candidates(
    filename: str,
    default_dir: Path,
    category: Category,
    cfg: Config,
    records: list[dict],
    cwd: Path,
) -> list[Candidate]:
    seen: set[Path] = set()
    out: list[Candidate] = []

    def add(path: Path, icon: str, note: str, kind: str) -> None:
        resolved = Path(path)
        if resolved in seen:
            return
        seen.add(resolved)
        out.append(Candidate(resolved, icon, note, kind))

    ext = _extension(filename)
    note = f"matched .{ext}" if ext and category.name != "other" else "default folder"
    add(default_dir, category.icon, note, "default")

    for path, count in recent_destinations(records):
        add(path, RECENT_ICON, f"used {count}×", "recent")

    for other in cfg.categories.values():
        add(other.dir, other.icon, "category", "category")

    add(cwd, CWD_ICON, "current dir", "cwd")
    return out


def _expanded(path: Path) -> Path | None:
    """Path("~s").expanduser() raises for an unknown user instead of passing the
    text through, so every expansion has to be guarded."""
    try:
        return path.expanduser()
    except RuntimeError:
        return None


def create_candidate(text: str) -> Candidate | None:
    value = text.strip()
    if not value or not value.startswith(PATHLIKE):
        return None
    target = _expanded(Path(value))
    if target is None or target.is_dir():
        return None
    return Candidate(target, CREATE_ICON, "create", "create")


def disk_candidates(text: str, limit: int = 6) -> list[Candidate]:
    """Directories on disk that complete what has been typed so far."""
    value = text.strip()
    if not value or not value.startswith(PATHLIKE):
        return []
    typed = Path(value)
    base, fragment = (typed, "") if value.endswith("/") else (typed.parent, typed.name)
    root = _expanded(base)
    if root is None or not root.is_dir():
        return []
    needle = fragment.lower()
    try:
        children = sorted(root.iterdir())
    except OSError:
        return []
    found = [
        child
        for child in children
        if child.is_dir()
        and child.name.lower().startswith(needle)
        and (needle.startswith(".") or not child.name.startswith("."))
    ]
    return [Candidate(child, DISK_ICON, "on disk", "disk") for child in found[:limit]]


def display_path(path: Path) -> str:
    """Collapse the home prefix. Matching and rendering both use this: on macOS
    every absolute path contains "/Users/", so filtering the raw path makes
    common queries like "ser" match everything."""
    home = str(Path.home())
    text = str(path)
    return "~" + text[len(home) :] if text.startswith(home) else text


def _subsequence(needle: str, haystack: str) -> bool:
    cursor = iter(haystack)
    return all(char in cursor for char in needle)


def filter_candidates(text: str, items: list[Candidate]) -> list[Candidate]:
    needle = text.strip().lower()
    if not needle:
        return list(items)
    return [c for c in items if _subsequence(needle, display_path(c.path).lower())]
