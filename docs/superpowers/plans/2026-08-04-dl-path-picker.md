# `dl` Interactive Path Picker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open a destination picker before `dl <url>` queues anything, preselecting the routed folder so `Enter` accepts it, and make an explicitly chosen destination survive completion.

**Architecture:** A pure `destinations` module builds and ranks candidates; a `PickerScreen` modal renders one file's choice; `PreviewApp` gains a picking phase that runs the modals in sequence and only then queues, inside the single Textual session it already opens.

**Tech Stack:** Python ≥3.11, `textual` (already a dependency), `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-dl-path-picker-design.md`

## Global Constraints

- **No new dependencies.** `textual` at runtime, `pytest` for dev. Not `fzf`, not `prompt_toolkit`.
- **No test may touch the network.** Bind `127.0.0.1` on an ephemeral port or use `tmp_path`.
- **Tests must not write outside `tmp_path`.** Use the `sandbox_cfg` fixture for anything that calls `mkdir` on a resolved destination.
- **aria2 JSON-RPC returns numbers as strings.** `int()` them at the boundary.
- **Comments: write none by default.** Repo `CLAUDE.md` — no restating code, no banners. Short docstrings only on public API.
- **Emoji only in fixed 2-cell columns**; degrade when `theme.icons` is False.
- **Textual merges `BINDINGS` across the MRO.** A subclass cannot remove an inherited key by declaring a shorter list; override the action method instead. (Verified experimentally while planning the preview feature.)
- **The picker runs only when `sys.stdout.isatty()`**, and never with `-d` or `--no-preview`.
- **`chosen=None` must leave `cmd_add` byte-for-byte as it is today.**

## File Structure

| Path | Responsibility |
|---|---|
| `downloader/dl/hook.py` | `relocate` leaves pinned destinations alone |
| `downloader/dl/destinations.py` | **new** — pure candidate building, ranking, filtering, writability |
| `downloader/dl/tui/picker.py` | **new** — `PickerScreen` |
| `downloader/dl/cli.py` | `cmd_add` accepts `chosen`; uses shared `ensure_writable` |
| `downloader/dl/tui/preview.py` | `PreviewApp` picking phase, `Request`, `run_preview` |
| `downloader/dl/__main__.py` | builds pending requests, supplies the queue callback |
| `downloader/tests/test_destinations.py` | **new** |
| `downloader/tests/test_picker.py` | **new** |

---

## Task 1: Pinned destinations survive completion

Fixes a live bug: `dl -d ~/anywhere` currently has its file moved into the category folder when the download finishes. Independently valuable, so it lands first.

**Files:**
- Modify: `downloader/dl/hook.py:57`
- Test: `downloader/tests/test_hook.py`

**Interfaces:**
- Consumes: `routing.resolve`, `routing.filename_from_url`
- Produces: `relocate(path, cfg, url)` unchanged in signature, changed in behaviour — a file outside the URL-routed directory is never moved

- [ ] **Step 1: Write the failing test**

Append to `downloader/tests/test_hook.py`:

```python
def test_relocate_leaves_an_explicitly_chosen_directory_alone(tmp_path, cfg):
    picked = tmp_path / "my-custom-folder"
    picked.mkdir()
    src = picked / "movie.mkv"
    src.write_text("data")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", tmp_path / "Movies", ("mkv",), "🎬", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})

    final = hook.relocate(src, routed, "https://e.com/movie.mkv")

    assert final == src
    assert src.exists()
    assert not (tmp_path / "Movies" / "movie.mkv").exists()


def test_relocate_still_corrects_a_late_learned_filename(tmp_path, cfg):
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", tmp_path / "Movies", ("mkv",), "🎬", "#fff")
    cats["iso"] = config.Category("iso", tmp_path / "ISO", ("iso",), "💿", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})

    landed = tmp_path / "ISO"
    landed.mkdir()
    src = landed / "surprise.mkv"
    src.write_text("data")

    final = hook.relocate(src, routed, "https://e.com/download.iso")

    assert final == tmp_path / "Movies" / "surprise.mkv"
    assert final.exists()
    assert not src.exists()


def test_relocate_is_a_noop_when_already_in_the_right_place(tmp_path, cfg):
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", tmp_path / "Movies", ("mkv",), "🎬", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})
    home = tmp_path / "Movies"
    home.mkdir()
    src = home / "movie.mkv"
    src.write_text("data")

    assert hook.relocate(src, routed, "https://e.com/movie.mkv") == src
    assert src.exists()
```

- [ ] **Step 2: Run to verify the first one fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_hook.py -k explicitly_chosen -v`
Expected: FAIL — the file was moved into `Movies`

- [ ] **Step 3: Implement**

In `downloader/dl/hook.py`, replace the opening of `relocate`:

```python
def relocate(path: Path, cfg: Config, url: str) -> Path:
    if not path.exists():
        return path
    target_dir = routing.resolve(url, path.name, cfg).path
    if target_dir == path.parent:
        return path
```

with:

```python
def relocate(path: Path, cfg: Config, url: str) -> Path:
    """Correct the destination when the real filename routes elsewhere.

    A file sitting outside the directory URL-based routing chose was pinned by
    -d or the picker, so it is left alone.
    """
    if not path.exists():
        return path
    routed = routing.resolve(url, routing.filename_from_url(url), cfg).path
    if path.parent != routed:
        return path
    target_dir = routing.resolve(url, path.name, cfg).path
    if target_dir == path.parent:
        return path
```

The rest of the function is unchanged.

- [ ] **Step 4: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/hook.py downloader/tests/test_hook.py
git commit -m "dl: keep explicitly chosen destinations on completion"
```

---

## Task 2: Destination candidates

**Files:**
- Create: `downloader/dl/destinations.py`
- Test: `downloader/tests/test_destinations.py`

**Interfaces:**
- Consumes: `dl.config.Config`, `dl.config.Category`
- Produces:
  - `Candidate(path: Path, icon: str, note: str, kind: str)` — frozen; `kind` is one of `default`, `recent`, `category`, `cwd`, `create`
  - `ensure_writable(path: Path) -> bool` — creates the directory, returns whether it is writable
  - `recent_destinations(records: list[dict], limit: int = 5) -> list[tuple[Path, int]]`
  - `candidates(filename, default_dir, category, cfg, records, cwd) -> list[Candidate]`
  - `create_candidate(text: str) -> Candidate | None`
  - `filter_candidates(text: str, items: list[Candidate]) -> list[Candidate]`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_destinations.py`:

```python
from pathlib import Path

import pytest

from dl import config, destinations
from dl.destinations import (
    Candidate,
    candidates,
    create_candidate,
    ensure_writable,
    filter_candidates,
    recent_destinations,
)


@pytest.fixture
def cfg():
    return config.defaults()


def rec(path):
    return {"path": path, "name": Path(path).name, "status": "ok"}


def test_recent_destinations_of_empty_history():
    assert recent_destinations([]) == []


def test_recent_destinations_counts_parent_directories():
    got = recent_destinations([rec("/a/x.mkv"), rec("/a/y.mkv"), rec("/b/z.iso")])
    assert got[0] == (Path("/a"), 2)
    assert got[1] == (Path("/b"), 1)


def test_recent_destinations_breaks_ties_by_most_recent():
    got = recent_destinations([rec("/old/a.mkv"), rec("/new/b.mkv")])
    assert [p for p, _ in got] == [Path("/new"), Path("/old")]


def test_recent_destinations_skips_records_without_a_path():
    got = recent_destinations([{"name": "x", "status": "error"}, rec("/a/x.mkv")])
    assert got == [(Path("/a"), 1)]


def test_recent_destinations_respects_the_limit():
    records = [rec(f"/d{i}/f.mkv") for i in range(10)]
    assert len(recent_destinations(records, limit=3)) == 3


def test_candidates_put_the_routed_default_first(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert items[0].path == Path("/movies")
    assert items[0].kind == "default"
    assert items[0].icon == "🎬"
    assert "mkv" in items[0].note


def test_candidates_note_for_an_uncategorised_file(cfg):
    from dl.routing import OTHER

    items = candidates("README", Path("/downloads"), OTHER, cfg, [], Path("/cwd"))
    assert items[0].note == "default folder"


def test_candidates_include_recents_after_the_default(cfg):
    records = [rec("/series/a.mkv"), rec("/series/b.mkv")]
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/cwd"))
    assert items[1].path == Path("/series")
    assert items[1].kind == "recent"
    assert items[1].note == "used 2×"


def test_candidates_deduplicate_a_recent_that_is_the_default(cfg):
    records = [rec("/movies/a.mkv")]
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/cwd"))
    assert [c.path for c in items].count(Path("/movies")) == 1


def test_candidates_include_other_categories(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    kinds = [c.kind for c in items]
    assert "category" in kinds
    assert cfg.categories["iso"].dir in [c.path for c in items]


def test_candidates_end_with_the_current_directory(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert items[-1].path == Path("/cwd")
    assert items[-1].kind == "cwd"
    assert items[-1].note == "current dir"


def test_candidates_never_repeat_a_path(cfg):
    records = [rec("/movies/a.mkv"), rec(str(cfg.categories["iso"].dir / "b.iso"))]
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/movies"))
    paths = [c.path for c in items]
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("text", ["/tmp/x", "~/stuff", "./here", "."])
def test_create_candidate_for_pathlike_text(text):
    made = create_candidate(text)
    assert made is not None
    assert made.kind == "create"
    assert made.note == "create"


@pytest.mark.parametrize("text", ["", "movies", "ser"])
def test_create_candidate_rejects_non_paths(text):
    assert create_candidate(text) is None


def test_create_candidate_expands_home():
    made = create_candidate("~/stuff")
    assert "~" not in str(made.path)


def test_filter_returns_everything_for_empty_text(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert filter_candidates("", items) == items


def test_filter_matches_a_subsequence(cfg):
    items = [
        Candidate(Path("/Users/me/Movies/Series"), "🕘", "used 2×", "recent"),
        Candidate(Path("/Users/me/Downloads/ISO"), "💿", "category", "category"),
    ]
    assert [c.path for c in filter_candidates("ser", items)] == [Path("/Users/me/Movies/Series")]


def test_filter_is_case_insensitive(cfg):
    items = [Candidate(Path("/Users/me/Movies"), "🎬", "x", "recent")]
    assert filter_candidates("MOVIES", items)


def test_filter_returns_empty_when_nothing_matches(cfg):
    items = [Candidate(Path("/a"), "🎬", "x", "recent")]
    assert filter_candidates("zzzz", items) == []


def test_ensure_writable_creates_the_directory(tmp_path):
    target = tmp_path / "deep" / "new"
    assert ensure_writable(target) is True
    assert target.is_dir()


def test_ensure_writable_is_false_for_an_uncreatable_path(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert ensure_writable(locked / "sub") is False
    finally:
        locked.chmod(0o700)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_destinations.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.destinations'`

- [ ] **Step 3: Implement**

Create `downloader/dl/destinations.py`:

```python
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .config import Category, Config

PATHLIKE = ("/", "~", ".")
RECENT_ICON = "🕘"
CWD_ICON = "📁"
CREATE_ICON = "✏️"


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


def create_candidate(text: str) -> Candidate | None:
    value = text.strip()
    if not value or not value.startswith(PATHLIKE):
        return None
    return Candidate(Path(value).expanduser(), CREATE_ICON, "create", "create")


def _subsequence(needle: str, haystack: str) -> bool:
    cursor = iter(haystack)
    return all(char in cursor for char in needle)


def filter_candidates(text: str, items: list[Candidate]) -> list[Candidate]:
    needle = text.strip().lower()
    if not needle:
        return list(items)
    return [c for c in items if _subsequence(needle, str(c.path).lower())]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_destinations.py -v`
Expected: PASS

- [ ] **Step 5: Share `ensure_writable` with the CLI**

In `downloader/dl/cli.py`, delete the private helper:

```python
def _ensure_writable(target: Path) -> bool:
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(target, os.W_OK)
```

Add `from .destinations import ensure_writable` to its imports, replace the one
call site `_ensure_writable(resolution.path)` with `ensure_writable(resolution.path)`,
and drop the now-unused `import os`.

- [ ] **Step 6: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add downloader/dl/destinations.py downloader/dl/cli.py downloader/tests/test_destinations.py
git commit -m "dl: add destination candidate ranking"
```

---

## Task 3: The picker screen

**Files:**
- Create: `downloader/dl/tui/picker.py`
- Test: `downloader/tests/test_picker.py`

**Interfaces:**
- Consumes: `dl.destinations`, `dl.theme.Theme`
- Produces: `PickerScreen(filename, default_dir, category, cfg, records, index, total, theme)` — a `ModalScreen[Path | None]` dismissing with the chosen directory, or `None` for "use the routed default"; attributes `visible: list[Candidate]`, `cursor: int`, `error: str`

`up`/`down` are bound with `priority=True` so the focused `Input` cannot swallow them.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_picker.py`:

```python
from pathlib import Path

import pytest
from textual.app import App, ComposeResult

from dl import config, theme
from dl.tui.picker import PickerScreen


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class Host(App):
    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "unset"

    def compose(self) -> ComposeResult:
        return []

    def on_mount(self):
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def make(cfg, tmp_path, records=(), filename="movie.mkv"):
    return PickerScreen(
        filename=filename,
        default_dir=tmp_path / "default",
        category=cfg.categories["video"],
        cfg=cfg,
        records=list(records),
        index=0,
        total=1,
        theme=theme.THEMES["aurora"],
    )


async def test_enter_accepts_the_preselected_default(cfg, tmp_path):
    app = Host(make(cfg, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == tmp_path / "default"


async def test_escape_dismisses_with_none(cfg, tmp_path):
    app = Host(make(cfg, tmp_path))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


async def test_down_then_enter_picks_the_second_candidate(cfg, tmp_path):
    records = [{"path": str(tmp_path / "series" / "a.mkv")}]
    screen = make(cfg, tmp_path, records)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        second = screen.visible[1].path
        await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == second


async def test_cursor_does_not_run_past_the_ends(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for _ in range(50):
            await pilot.press("down")
        assert screen.cursor == len(screen.visible) - 1
        for _ in range(50):
            await pilot.press("up")
        assert screen.cursor == 0


async def test_typing_filters_the_list(cfg, tmp_path):
    records = [{"path": str(tmp_path / "series" / "a.mkv")}]
    screen = make(cfg, tmp_path, records)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(screen.visible)
        for ch in "series":
            await pilot.press(ch)
        await pilot.pause()
        assert len(screen.visible) < before
        assert any("series" in str(c.path) for c in screen.visible)


async def test_typing_a_path_offers_a_create_row(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for ch in "~/brand-new":
            await pilot.press(ch)
        await pilot.pause()
        assert screen.visible[-1].kind == "create"


async def test_accepting_a_create_row_returns_the_typed_path(cfg, tmp_path):
    from textual.widgets import Input

    screen = make(cfg, tmp_path)
    app = Host(screen)
    target = tmp_path / "brand-new"
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.query_one("#picker-input", Input).value = str(target)
        await pilot.pause()
        screen.cursor = len(screen.visible) - 1
        await pilot.press("enter")
        await pilot.pause()
    assert app.result == target


async def test_an_unwritable_choice_shows_an_error_and_stays_open(cfg, tmp_path):
    from textual.widgets import Input

    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    screen = make(cfg, tmp_path)
    app = Host(screen)
    try:
        async with app.run_test() as pilot:
            await pilot.pause()
            screen.query_one("#picker-input", Input).value = str(locked / "sub")
            await pilot.pause()
            screen.cursor = len(screen.visible) - 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.result == "unset"
            assert "cannot write" in screen.error
    finally:
        locked.chmod(0o700)


async def test_tab_completes_the_highlighted_path_into_the_input(cfg, tmp_path):
    screen = make(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert str(tmp_path / "default") in screen.input_value


async def test_header_shows_filename_and_position(cfg, tmp_path):
    screen = PickerScreen(
        filename="ubuntu.iso",
        default_dir=tmp_path / "iso",
        category=cfg.categories["iso"],
        cfg=cfg,
        records=[],
        index=1,
        total=3,
        theme=theme.THEMES["aurora"],
    )
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "ubuntu.iso" in screen.header_text
        assert "2 of 3" in screen.header_text
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_picker.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui.picker'`

- [ ] **Step 3: Implement**

Create `downloader/dl/tui/picker.py`:

```python
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from ..config import Category, Config
from ..destinations import (
    Candidate,
    candidates,
    create_candidate,
    ensure_writable,
    filter_candidates,
)
from ..theme import Theme, icon_for
from .table import escape

MAX_ROWS = 8
PICKER_HINT = "⏎ accept    ↑↓ choose    esc use default    ^C cancel all"


class PickerScreen(ModalScreen[Path | None]):
    BINDINGS = [
        ("escape", "use_default", "default"),
        Binding("up", "move_up", "up", priority=True),
        Binding("down", "move_down", "down", priority=True),
        Binding("tab", "complete", "complete", priority=True),
    ]

    def __init__(
        self,
        filename: str,
        default_dir: Path,
        category: Category,
        cfg: Config,
        records: list[dict],
        index: int,
        total: int,
        theme: Theme,
    ):
        super().__init__()
        self.filename = filename
        self.default_dir = default_dir
        self.category = category
        self.cfg = cfg
        self.records = records
        self.index = index
        self.total = total
        self.theme_data = theme
        self.all_candidates = candidates(
            filename, default_dir, category, cfg, records, Path.cwd()
        )
        self.visible: list[Candidate] = list(self.all_candidates)
        self.cursor = 0
        self.error = ""
        self.input_value = ""

    @property
    def header_text(self) -> str:
        position = f"file {self.index + 1} of {self.total}"
        return f"  Save  {self.filename}          {position}"

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static(self.header_text, id="picker-head")
            yield Input(placeholder="filter, or type a path…", id="picker-input")
            yield Static("", id="picker-list")
            yield Static("", id="picker-error")
            yield Static(PICKER_HINT, id="picker-hint")

    def on_mount(self) -> None:
        self.query_one("#picker-input", Input).focus()
        self._render()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.input_value = event.value
        self._rebuild()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._accept()

    def _rebuild(self) -> None:
        items = filter_candidates(self.input_value, self.all_candidates)
        made = create_candidate(self.input_value)
        if made is not None:
            items = items + [made]
        self.visible = items
        self.cursor = min(self.cursor, max(len(items) - 1, 0))
        self.error = ""
        self._render()

    def _render(self) -> None:
        rows = []
        for position, item in enumerate(self.visible[:MAX_ROWS]):
            selected = position == self.cursor
            marker = "▌" if selected else " "
            icon = item.icon if self.theme_data.icons else item.kind[:2].upper().ljust(2)
            shown = escape(str(item.path).replace(str(Path.home()), "~"))
            line = f"{marker} {icon}  {shown:<44} {item.note}"
            rows.append(
                line if self.theme_data.mono or not selected
                else f"[{self.theme_data.accent}]{line}[/]"
            )
        self.query_one("#picker-list", Static).update("\n".join(rows) or "  (no match)")
        self.query_one("#picker-error", Static).update(
            f"  ⚠ {self.error}" if self.error else ""
        )

    def action_move_down(self) -> None:
        if self.visible:
            self.cursor = min(self.cursor + 1, len(self.visible) - 1)
            self._render()

    def action_move_up(self) -> None:
        if self.visible:
            self.cursor = max(self.cursor - 1, 0)
            self._render()

    def action_complete(self) -> None:
        if not self.visible:
            return
        field = self.query_one("#picker-input", Input)
        field.value = str(self.visible[self.cursor].path)
        self.input_value = field.value

    def action_use_default(self) -> None:
        self.dismiss(None)

    def _accept(self) -> None:
        if not self.visible:
            return
        chosen = self.visible[min(self.cursor, len(self.visible) - 1)].path
        if not ensure_writable(chosen):
            self.error = f"cannot write to {chosen}"
            self._render()
            return
        self.dismiss(chosen)
```

- [ ] **Step 4: Add the modal styling**

In `downloader/dl/tui/app.py`, extend the CSS. Replace:

```python
AddUrlModal, SpeedLimitModal, ConfirmModal, DeleteModal { align: center middle; }
#add-box, #limit-box, #confirm-box, #delete-box {
    width: 70; padding: 1 2; border: round $accent; background: $surface;
}
```

with:

```python
AddUrlModal, SpeedLimitModal, ConfirmModal, DeleteModal, PickerScreen {
    align: center middle;
}
#add-box, #limit-box, #confirm-box, #delete-box, #picker-box {
    width: 76; padding: 1 2; border: round $accent; background: $surface;
}
#picker-list { height: auto; }
#picker-error { height: auto; color: $error; }
#picker-hint { color: $text-muted; }
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_picker.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add downloader/dl/tui/picker.py downloader/dl/tui/app.py downloader/tests/test_picker.py
git commit -m "dl: add interactive destination picker screen"
```

---

## Task 4: `cmd_add` accepts chosen destinations

**Files:**
- Modify: `downloader/dl/cli.py`
- Test: `downloader/tests/test_cli.py`

**Interfaces:**
- Consumes: `dl.routing.Resolution`
- Produces: `cmd_add(urls, cfg, client, explicit_dir, chosen: list[Path | None] | None = None) -> tuple[int, list[str]]`

A chosen directory overrides the destination but keeps the routed category, so the queued line still shows the filetype icon rather than the generic fallback.

- [ ] **Step 1: Write the failing test**

Append to `downloader/tests/test_cli.py`:

```python
def test_cmd_add_uses_a_chosen_directory(cfg, tmp_path):
    client = FakeClient()
    target = tmp_path / "picked"
    rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[target])
    assert rc == 0
    assert client.added[0][1]["dir"] == str(target)
    assert target.is_dir()


def test_cmd_add_chosen_none_entry_falls_back_to_routing(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[None])
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)


def test_cmd_add_chosen_applies_positionally(cfg, tmp_path):
    client = FakeClient()
    first = tmp_path / "one"
    cli.cmd_add(
        ["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None, chosen=[first, None]
    )
    assert client.added[0][1]["dir"] == str(first)
    assert client.added[1][1]["dir"] == str(cfg.categories["video"].dir)


def test_cmd_add_keeps_the_filetype_icon_for_a_chosen_directory(cfg, tmp_path, capsys):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None, chosen=[tmp_path / "picked"])
    assert "💿" in capsys.readouterr().out


def test_cmd_add_chosen_wins_over_explicit_dir(cfg, tmp_path):
    client = FakeClient()
    cli.cmd_add(
        ["https://e.com/a.iso"], cfg, client, tmp_path / "flag", chosen=[tmp_path / "picked"]
    )
    assert client.added[0][1]["dir"] == str(tmp_path / "picked")


def test_cmd_add_without_chosen_is_unchanged(cfg):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, None)
    assert rc == 0
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_cli.py -k chosen`
Expected: FAIL — `TypeError: cmd_add() got an unexpected keyword argument 'chosen'`

- [ ] **Step 3: Implement**

In `downloader/dl/cli.py`, add `Resolution` to the routing import if absent, then replace the body of `cmd_add` from the signature through the loop:

```python
def cmd_add(
    urls: list[str],
    cfg: Config,
    client,
    explicit_dir: Path | None,
    chosen: list[Path | None] | None = None,
) -> tuple[int, list[str]]:
    if not urls:
        print("dl: no URLs given", file=sys.stderr)
        return 1, []
    bad = [u for u in urls if not looks_like_url(u)]
    if bad:
        for value in bad:
            print(f"dl: not a URL: {value!r}", file=sys.stderr)
        print("dl: run `dl --help` for usage", file=sys.stderr)
        return 1, []
    failures = 0
    gids: list[str] = []
    for index, url in enumerate(urls):
        name = routing.filename_from_url(url)
        routed = routing.resolve(url, name, cfg)
        pick = chosen[index] if chosen and index < len(chosen) else None
        target = pick or explicit_dir or routed.path
        resolution = Resolution(Path(target), routed.category)
        if not ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        gids.append(client.add_uri([url], add_options(cfg, resolution)))
        print(f"  {resolution.category.icon} queued  {name or url}  →  {resolution.path}")
    return (1 if failures else 0), gids
```

- [ ] **Step 4: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS — including every pre-existing `cmd_add` test

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/cli.py downloader/tests/test_cli.py
git commit -m "dl: let cmd_add take per-url chosen destinations"
```

---

## Task 5: `PreviewApp` picking phase

**Files:**
- Modify: `downloader/dl/tui/preview.py`
- Test: `downloader/tests/test_preview.py`

**Interfaces:**
- Consumes: `PickerScreen` (Task 3)
- Produces:
  - `Request(url: str, filename: str, default_dir: Path, category: Category)` — frozen dataclass
  - `PreviewApp(cfg, client, gids=(), pending=(), queue=None)` — new attributes `pending`, `queue`, `picking: bool`, `chosen: list[Path | None]`
  - `run_preview(cfg, client, gids=(), pending=(), queue=None) -> list[str]`

**The trap:** `_after_refresh` exits when nothing is watched. During picking `watch` is empty, so without the `picking` guard the app would exit before a picker was ever seen.

- [ ] **Step 1: Write the failing test**

Append to `downloader/tests/test_preview.py`:

```python
from pathlib import Path

from dl.tui.preview import Request


def request(tmp_path, cfg, name="movie.mkv"):
    return Request(
        url=f"https://e.com/{name}",
        filename=name,
        default_dir=tmp_path / "default",
        category=cfg.categories["video"],
    )


async def test_picking_shows_one_screen_per_pending_file(cfg, tmp_path):
    client = PreviewClient(active=())
    seen = []

    def queue(chosen):
        seen.append(list(chosen))
        return []

    app = PreviewApp(
        cfg,
        client,
        pending=[request(tmp_path, cfg, "a.mkv"), request(tmp_path, cfg, "b.mkv")],
        queue=queue,
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.picking is True
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert seen == [[tmp_path / "default", tmp_path / "default"]]


async def test_picking_does_not_exit_the_app_before_queuing(cfg, tmp_path):
    client = PreviewClient(active=())
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: [])
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True
        assert app.picking is True


async def test_escape_records_none_and_moves_on(cfg, tmp_path):
    client = PreviewClient(active=())
    seen = []
    app = PreviewApp(
        cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: seen.append(list(c)) or []
    )
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert seen == [[None]]


async def test_queue_result_becomes_the_watch_set(cfg, tmp_path):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert app.picking is False
        assert app.watch == {"g1"}
        assert app.is_running is True


async def test_app_exits_when_queuing_produced_nothing(cfg, tmp_path):
    client = PreviewClient(active=())
    app = PreviewApp(cfg, client, pending=[request(tmp_path, cfg)], queue=lambda c: [])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert app.is_running is False


async def test_gids_only_construction_still_works(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.picking is False
        assert app.watch == {"g1"}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -k picking`
Expected: FAIL — `ImportError: cannot import name 'Request'`

- [ ] **Step 3: Implement**

In `downloader/dl/tui/preview.py`, add to the imports. `history` is required —
`_ask` reads past destinations through it, and `preview.py` does not import it
today:

```python
from dataclasses import dataclass

from .. import history
from ..config import Category
from .picker import PickerScreen
```

Add the request type beside `PREVIEW_HINT`:

```python
@dataclass(frozen=True)
class Request:
    url: str
    filename: str
    default_dir: Path
    category: Category
```

Replace `PreviewApp.__init__` and `on_mount`, and add the picking helpers:

```python
    def __init__(self, cfg, client, gids=(), pending=(), queue=None):
        super().__init__(cfg, client)
        self.watch = set(gids)
        self.results: list[dict] = []
        self.hint_text = PREVIEW_HINT
        self.pending = list(pending)
        self.queue = queue
        self.picking = bool(self.pending)
        self.chosen: list[Path | None] = []

    def on_mount(self) -> None:
        super().on_mount()
        self.hint.update(PREVIEW_HINT)
        if self.pending:
            self._ask(0)

    def _ask(self, index: int) -> None:
        if index >= len(self.pending):
            self._finish_picking()
            return
        item = self.pending[index]

        def chosen(value):
            self.chosen.append(value)
            self._ask(index + 1)

        self.push_screen(
            PickerScreen(
                filename=item.filename,
                default_dir=item.default_dir,
                category=item.category,
                cfg=self.cfg,
                records=history.tail(self.history_log, 200),
                index=index,
                total=len(self.pending),
                theme=self.theme_data,
            ),
            chosen,
        )

    def _finish_picking(self) -> None:
        self.picking = False
        gids = self.queue(self.chosen) if self.queue else []
        self.watch = set(gids)
        if not self.watch:
            self.exit()
```

Change `_after_refresh` to respect the guard:

```python
    def _after_refresh(self, items: list[dict]) -> None:
        if self.picking or items:
            return
        self.results = self._collect_results()
        self.exit()
```

Change `run_preview`:

```python
def run_preview(cfg, client, gids=(), pending=(), queue=None) -> list[str]:
    app = PreviewApp(cfg, client, gids, pending, queue)
    app.run()
    return summarise(app.results, icons=select(cfg).icons)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add downloader/dl/tui/preview.py downloader/tests/test_preview.py
git commit -m "dl: run the destination picker before queuing"
```

---

## Task 6: Dispatch, docs, and a real picked download

**Files:**
- Modify: `downloader/dl/__main__.py`
- Modify: `downloader/tests/test_main.py`
- Modify: `downloader/tests/test_integration.py`
- Modify: `downloader/README.md`, `README.md`

**Interfaces:**
- Consumes: `Request`, `run_preview(..., pending=, queue=)`, `cmd_add(..., chosen=)`
- Produces: final user-facing behaviour

- [ ] **Step 1: Write the failing dispatch tests**

Append to `downloader/tests/test_main.py`:

```python
def test_interactive_run_goes_through_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert calls, "run_preview was not called"
    kwargs = calls[0]
    assert kwargs.get("pending"), "no pending requests were passed"
    assert kwargs.get("queue") is not None


def test_explicit_dir_skips_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    entry.main(["-d", str(tmp_path / "x"), "https://e.com/a.iso"])
    assert calls
    assert not calls[0].get("pending")


def test_no_preview_skips_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    entry.main(["--no-preview", "https://e.com/a.iso"])
    assert calls == []


def test_non_tty_skips_the_picker(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, False, calls)
    entry.main(["https://e.com/a.iso"])
    assert calls == []
```

Replace the existing `_wire` helper so it records keyword arguments:

```python
def _wire(monkeypatch, tmp_path, isatty, calls):
    from dl import cli, config, daemon

    monkeypatch.setattr(entry, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "ensure_running", lambda *a, **k: StubClient())
    monkeypatch.setattr(daemon, "bump_generation", lambda *a, **k: 1)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (0, ["gidX"]))
    monkeypatch.setattr("sys.stdout.isatty", lambda: isatty)

    def fake_preview(cfg, client, gids=(), pending=(), queue=None):
        calls.append({"gids": gids, "pending": pending, "queue": queue})
        if queue is not None:
            queue([None] * len(pending))
        return ["  done"]

    monkeypatch.setattr(entry, "run_preview", fake_preview)
```

Update the four pre-existing preview dispatch tests that assert on `calls`
(`test_url_with_a_tty_attaches_the_preview`, `test_url_without_a_tty_does_not_attach`,
`test_no_preview_flag_suppresses_attachment`, `test_partial_add_still_attaches_for_the_successes`)
— they still assert `len(calls) == 1` or `calls == []`, which the dict form
satisfies unchanged.

- [ ] **Step 2: Run to verify they fail**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_main.py -k picker`
Expected: FAIL — `pending` is empty because dispatch does not build requests yet

- [ ] **Step 3: Wire dispatch**

In `downloader/dl/__main__.py`, extend the imports:

```python
from . import cli, config, daemon, routing
from .tui.preview import Request, run_preview
```

Replace the whole `if urls:` block with:

```python
    if urls:
        daemon.bump_generation(config.STATE_DIR)
        interactive = preview and sys.stdout.isatty()
        if not interactive:
            rc, _gids = cli.cmd_add(urls, cfg, client, explicit_dir)
            return rc

        pending = []
        if explicit_dir is None:
            for url in urls:
                name = routing.filename_from_url(url)
                resolved = routing.resolve(url, name, cfg)
                pending.append(
                    Request(url, name or url, resolved.path, resolved.category)
                )

        outcome = {"rc": 0}

        def queue(chosen):
            rc, gids = cli.cmd_add(urls, cfg, client, explicit_dir, chosen or None)
            outcome["rc"] = rc
            return gids

        if not pending:
            rc, gids = cli.cmd_add(urls, cfg, client, explicit_dir)
            outcome["rc"] = rc
            if not gids:
                return rc
            for line in run_preview(cfg, client, gids=gids):
                print(line)
            return outcome["rc"]

        for line in run_preview(cfg, client, pending=pending, queue=queue):
            print(line)
        return outcome["rc"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Add the integration test**

Append to `downloader/tests/test_integration.py`:

```python
def test_a_picked_destination_survives_completion(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    picked = state.parent / "hand-picked"
    picked.mkdir()

    url = f"{fileserver}/sample.iso"
    gid = client.add_uri(
        [url], {**add_options(cfg, routing.resolve(url, "sample.iso", cfg)), "dir": str(picked)}
    )

    target = picked / "sample.iso"
    assert wait_for(lambda: target.exists() and target.stat().st_size == len(PAYLOAD))
    assert wait_for(lambda: client.tell_status(gid)["status"] == "complete")

    log = state / "history.jsonl"
    assert wait_for(lambda: log.exists() and history.tail(log, 5))
    record = history.tail(log, 5)[-1]
    assert Path(record["path"]).parent == picked
    assert not (cfg.categories["iso"].dir / "sample.iso").exists()
```

Add `from pathlib import Path` to that file's imports if absent.

- [ ] **Step 6: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 7: Document it**

In `downloader/README.md`, after the paragraph describing the preview, insert:

```markdown
Before anything is queued, `dl` asks where to put each file. The routed folder is
preselected, so `Enter` accepts it; `↑` `↓` choose another, typing filters the
list, and typing a path that starts with `/`, `~`, or `.` offers to create it.
`Esc` takes the default, `Ctrl-C` cancels before anything is queued.

Candidates are the routed folder, folders you have used recently, the other
category folders, and the current directory. Recents come from your download
history, so the list improves as you use it.

`-d`, `--no-preview`, and piped output all skip the picker.
```

Add the picker keys to the Keys table in the same file, and mention the picker in
the `dl` section of the root `README.md`.

- [ ] **Step 8: Verify by hand**

```bash
cd downloader && make install
dl --no-preview https://speed.hetzner.de/100MB.bin   # no picker, exits at once
dl -d /tmp/pick-test https://speed.hetzner.de/100MB.bin  # no picker, goes to /tmp/pick-test
dl https://speed.hetzner.de/100MB.bin                # picker appears, Enter accepts
```

Expected: the first two never show a picker; the third shows it with the ISO
folder preselected, and `Enter` slides into the live preview.

- [ ] **Step 9: Commit**

```bash
git add downloader/dl/__main__.py downloader/tests/test_main.py downloader/tests/test_integration.py downloader/README.md README.md
git commit -m "dl: ask where to save before queuing"
```

---

## Self-Review Notes

**Spec coverage.** §1 flow → Tasks 5 and 6; `-d`/`--no-preview`/non-TTY skips → Task 6; the `cmd_add` contract → Task 4; pinned destinations and the proven `-d` bug → Task 1; §2 picker screen, candidate order, keys, validation → Tasks 2 and 3; §3 components → Tasks 2, 3, 5; §3 picking trap → Task 5 (`picking` guard plus a dedicated test); §4 failure table → Tasks 1, 3, 5, 6; §5 tests → all six tasks.

**Ordering rationale.** Task 1 lands first because it fixes a live bug independently of the picker, and because the picker is pointless until a chosen destination survives completion.

**Verified experimentally while planning.** Two assumptions the tests depend on
were checked against the installed Textual rather than assumed:
- `priority=True` bindings **do** beat a focused `Input` for `up`/`down`, so the
  picker's cursor keys work while the filter field has focus.
- `pilot.press("~")` and `pilot.press("/")` type those characters literally — no
  `"tilde"` / `"slash"` key names needed.

Setting `Input.value` directly (used for the two long-path tests) fires
`Input.Changed`, so the candidate list rebuilds exactly as it does when typing.

**Known risks.**
- `PickerScreen` builds candidates once in `__init__`. A test that changes the
  filesystem afterwards will not see new candidates; none of the supplied tests
  rely on that.
- `PickerScreen` reads `Path.cwd()` at construction. Tests must not assume a particular working directory — the supplied tests only assert the cwd candidate's `kind` and `note`.
- Task 6's dispatch block has two `cmd_add` paths (with and without a picker). They must stay in sync on the `explicit_dir` argument; the dispatch tests cover both.
