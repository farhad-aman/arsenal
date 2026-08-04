# `dl` Download Manager Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `dl`, a terminal download manager with an animated Textual TUI and a fire-and-forget CLI, driving a lazily-spawned `aria2c` RPC daemon that owns all transfer work.

**Architecture:** `aria2c --enable-rpc` is the only long-lived process; it owns the queue, transfers, resume, throttling and torrents. The `dl` package is a stateless JSON-RPC client that owns routing policy and presentation only. A generated shell shim invokes `dl.hook` on download completion to append history, fire a notification, and arm an idle shutdown arbitrated by a generation counter.

**Tech Stack:** Python ≥3.11 (stdlib `tomllib`, `urllib.request`), `textual` (only runtime dependency), `pytest` (dev only), `aria2c` ≥1.36 (external binary).

**Spec:** `docs/superpowers/specs/2026-08-04-dl-downloader-design.md`

## Global Constraints

- **Python ≥3.11.** `tomllib` is stdlib from 3.11. Do not add a TOML backport.
- **`textual` is the ONLY runtime dependency.** RPC uses stdlib `urllib.request`; config uses stdlib `tomllib`. Never add `requests`, `httpx`, `rich` (Textual vendors Rich), `aria2p`, or `pydantic`.
- **`pytest` is the only dev dependency.** No `pytest-asyncio` — Textual ships its own async test harness; use `pytest`'s built-in support via a tiny `asyncio.run` wrapper as shown in Task 15.
- **No test may touch the network.** Every test binds `127.0.0.1` on an ephemeral port or uses `tmp_path`.
- **No module-level mutable state.** Every unit receives `Config` as a parameter. Module-level constants are fine.
- **Comments: write none by default.** This repo's `CLAUDE.md` mandates minimal comments — no comments restating code, no section banners, no docstrings on obvious functions. Short docstrings on public API surface only. Rewrite unclear code rather than explaining it.
- **Emoji only in fixed 2-cell reserved columns.** Never inline in a sentence. All other decoration uses 1-cell Unicode block/braille glyphs.
- **aria2 JSON-RPC returns all numbers as strings.** Always `int()` `totalLength`, `completedLength`, `downloadSpeed`, `numActive`, `numWaiting`, `connections`, `errorCode` at the boundary. This is the single most likely source of bugs in this project.
- **RPC binds `127.0.0.1` only**, always with `--rpc-secret`; the secret file is mode `0600`.
- **Target platform is macOS.** `pbpaste` and `osascript` are assumed present.
- **Dependency direction is one-way.** `format`, `config`, `routing`, `history`, `theme` are leaves. `rpc` depends on nothing internal. `daemon` → `rpc`. `cli`/`watch`/`hook` → `daemon`. `tui` → `rpc`, `config`, `theme`, `format`. No cycles.

## File Structure

All new code lives in `downloader/`, mirroring how `vpn/` holds its tool.

| Path | Responsibility |
|---|---|
| `downloader/pyproject.toml` | package metadata, deps, pytest config |
| `downloader/Makefile` | `install`, `test`, `uninstall` |
| `downloader/dl/__init__.py` | version constant only |
| `downloader/dl/__main__.py` | argv dispatch, nothing else |
| `downloader/dl/format.py` | pure display formatters — bytes, duration, speed, sparkline, bar |
| `downloader/dl/config.py` | XDG paths, dataclasses, TOML load, default file writer |
| `downloader/dl/routing.py` | `(url, filename, config) → (Path, Category)`, pure |
| `downloader/dl/history.py` | JSONL append and tail |
| `downloader/dl/rpc.py` | aria2 JSON-RPC client |
| `downloader/dl/daemon.py` | binary check, port selection, secret, hook shims, spawn, generation counter |
| `downloader/dl/hook.py` | `--on-download-complete/-error` entry point |
| `downloader/dl/cli.py` | non-TUI subcommands |
| `downloader/dl/watch.py` | clipboard poller |
| `downloader/dl/theme.py` | theme palettes and glyph sets |
| `downloader/dl/tui/app.py` | Textual App, layout, keymap, refresh loop |
| `downloader/dl/tui/table.py` | `DownloadTable` widget, `Row` dataclass |
| `downloader/dl/tui/status.py` | `StatusBar` widget |
| `downloader/dl/tui/modals.py` | `AddUrlModal`, `SpeedLimitModal`, `ConfirmModal` |
| `downloader/tests/*` | mirrors the module layout |

**Two modules are added beyond the spec's component list:** `format.py` (the spec's testing section requires formatters but §2 omitted the module) and `watch.py` (the spec placed `dl watch` in `cli.py`; splitting it keeps `cli.py` focused and makes the poller testable without a TTY). `theme.py` is the spec's "theme is data, not code" made concrete.

---

## Task 1: Scaffold, packaging, and install

**Files:**
- Create: `downloader/pyproject.toml`
- Create: `downloader/Makefile`
- Create: `downloader/dl/__init__.py`
- Create: `downloader/tests/test_smoke.py`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: nothing
- Produces: `dl.__version__: str`; a working `make install` and `make test`; the venv at `~/.local/share/dl/venv` and shim at `~/.local/bin/dl` that every later task assumes.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_smoke.py`:

```python
import dl


def test_version_is_a_dotted_string():
    parts = dl.__version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && python3 -m pytest tests/test_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl'`

- [ ] **Step 3: Create the package and packaging metadata**

Create `downloader/dl/__init__.py`:

```python
__version__ = "0.1.0"
```

Create `downloader/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "dl-downloader"
version = "0.1.0"
description = "Terminal download manager over aria2c"
requires-python = ">=3.11"
dependencies = ["textual>=0.80"]

[project.optional-dependencies]
dev = ["pytest>=8"]

[tool.setuptools.packages.find]
include = ["dl*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 4: Create the Makefile**

Create `downloader/Makefile`:

```makefile
VENV  := $(HOME)/.local/share/dl/venv
PY    := $(VENV)/bin/python
SHIM  := $(HOME)/.local/bin/dl

.PHONY: install test uninstall

install: $(PY)
	$(PY) -m pip install -q -e .
	@mkdir -p $(dir $(SHIM))
	@printf '#!/bin/sh\nexec %s -m dl "$$@"\n' "$(PY)" > $(SHIM)
	@chmod 755 $(SHIM)
	@echo "installed: $(SHIM)"

$(PY):
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip

test: $(PY)
	$(PY) -m pip install -q -e ".[dev]"
	$(PY) -m pytest

uninstall:
	rm -rf $(VENV) $(SHIM)
```

- [ ] **Step 5: Ignore build artifacts**

Append to `.gitignore`:

```
__pycache__/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd downloader && make test`
Expected: PASS — 1 passed

- [ ] **Step 7: Commit**

```bash
git add downloader/pyproject.toml downloader/Makefile downloader/dl/__init__.py downloader/tests/test_smoke.py .gitignore
git commit -m "dl: scaffold package, venv install, pytest wiring"
```

---

## Task 2: Display formatters

**Files:**
- Create: `downloader/dl/format.py`
- Test: `downloader/tests/test_format.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `human_bytes(n: int) -> str`
  - `human_speed(bps: int) -> str`
  - `human_duration(seconds: int) -> str`
  - `sparkline(samples: Sequence[int], width: int) -> str`
  - `progress_bar(pct: float, width: int) -> str`
  - `BLOCKS: str` (the 8 sparkline glyphs), `SPINNER: str` (the 10 braille glyphs)

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_format.py`:

```python
import pytest

from dl.format import (
    BLOCKS,
    SPINNER,
    human_bytes,
    human_duration,
    human_speed,
    progress_bar,
    sparkline,
)


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (2202009, "2.1 MB"),
        (922746880, "880 MB"),
        (6127219712, "5.7 GB"),
        (12884901888, "12 GB"),
    ],
)
def test_human_bytes(n, expected):
    assert human_bytes(n) == expected


def test_human_bytes_negative_is_dash():
    assert human_bytes(-1) == "—"


@pytest.mark.parametrize(
    "s,expected",
    [(0, "0s"), (3, "3s"), (59, "59s"), (201, "3m 21s"), (302, "5m 02s"), (683, "11m 23s"), (5025, "1h 23m")],
)
def test_human_duration(s, expected):
    assert human_duration(s) == expected


def test_human_duration_negative_is_dash():
    assert human_duration(-1) == "—"


def test_human_speed_appends_per_second():
    assert human_speed(8493465) == "8.1 MB/s"
    assert human_speed(0) == "0 B/s"


def test_sparkline_uses_all_eight_levels_across_a_ramp():
    line = sparkline(list(range(8)), 8)
    assert line == BLOCKS


def test_sparkline_flat_zero_is_lowest_block():
    assert sparkline([0, 0, 0], 3) == BLOCKS[0] * 3


def test_sparkline_pads_left_when_short_of_width():
    assert sparkline([7], 4) == BLOCKS[0] * 3 + BLOCKS[7]


def test_sparkline_keeps_most_recent_when_over_width():
    assert sparkline([0, 0, 0, 7], 2) == BLOCKS[0] + BLOCKS[7]


def test_sparkline_zero_width_is_empty():
    assert sparkline([1, 2, 3], 0) == ""


def test_progress_bar_full_is_all_solid():
    assert progress_bar(100.0, 10) == "█" * 10


def test_progress_bar_empty_has_no_solid_and_no_comet():
    assert progress_bar(0.0, 10) == "░" * 10


def test_progress_bar_has_comet_tail_after_body():
    bar = progress_bar(50.0, 10)
    assert bar == "█████▓▒░░░"
    assert len(bar) == 10


def test_progress_bar_comet_truncates_near_the_end():
    assert progress_bar(90.0, 10) == "█████████▓"


@pytest.mark.parametrize("width", range(4, 41))
@pytest.mark.parametrize("pct", [0, 1, 33.3, 50, 66.6, 99, 100])
def test_progress_bar_always_exact_width(width, pct):
    assert len(progress_bar(pct, width)) == width


def test_progress_bar_clamps_out_of_range():
    assert progress_bar(150.0, 5) == "█" * 5
    assert progress_bar(-10.0, 5) == "░" * 5


def test_spinner_has_ten_frames():
    assert len(SPINNER) == 10
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.format'`

- [ ] **Step 3: Implement**

Create `downloader/dl/format.py`:

```python
from collections.abc import Sequence

BLOCKS = "▁▂▃▄▅▆▇█"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
DASH = "—"

_UNITS = ("B", "KB", "MB", "GB", "TB", "PB")


def human_bytes(n: int) -> str:
    if n < 0:
        return DASH
    value = float(n)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
        value /= 1024
    return DASH


def human_speed(bps: int) -> str:
    return f"{human_bytes(max(bps, 0))}/s"


def human_duration(seconds: int) -> str:
    if seconds < 0:
        return DASH
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60:02d}s"
    return f"{seconds // 3600}h {(seconds % 3600) // 60:02d}m"


def sparkline(samples: Sequence[int], width: int) -> str:
    if width <= 0:
        return ""
    window = list(samples)[-width:]
    window = [0] * (width - len(window)) + window
    peak = max(window)
    if peak <= 0:
        return BLOCKS[0] * width
    return "".join(BLOCKS[min(len(BLOCKS) - 1, v * (len(BLOCKS) - 1) // peak)] for v in window)


def progress_bar(pct: float, width: int) -> str:
    if width <= 0:
        return ""
    ratio = min(max(pct, 0.0), 100.0) / 100.0
    body = int(ratio * width)
    if body >= width:
        return "█" * width
    if body <= 0:
        return "░" * width
    comet = "▓▒░"[: width - body]
    return "█" * body + comet + "░" * (width - body - len(comet))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS — all `test_format` tests green

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/format.py downloader/tests/test_format.py
git commit -m "dl: add display formatters"
```

---

## Task 3: Config

**Files:**
- Create: `downloader/dl/config.py`
- Test: `downloader/tests/test_config.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `CONFIG_DIR: Path`, `STATE_DIR: Path`, `CONFIG_FILE: Path`
  - `Category(name, dir, ext, icon, hue)` — frozen, `ext` is `tuple[str, ...]`
  - `General(default_dir, max_concurrent, idle_timeout, theme, ascii_icons, notify)` — `idle_timeout` is **int seconds**
  - `Limits(global_rate, per_download, connections, splits, min_split)` — rates are aria2 strings, `"0"` means unlimited
  - `Config(general, limits, categories: dict[str, Category], domains: dict[str, str])`
  - `load(path: Path | None = None) -> Config`
  - `write_default(path: Path) -> None`
  - `parse_duration(text: str) -> int`
  - `parse_rate(text: str) -> str`
  - `DEFAULT_CATEGORIES: dict[str, Category]`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_config.py`:

```python
import pytest

from dl import config


@pytest.mark.parametrize(
    "text,seconds", [("30s", 30), ("10m", 600), ("2h", 7200), ("45", 45), ("0", 0)]
)
def test_parse_duration(text, seconds):
    assert config.parse_duration(text) == seconds


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        config.parse_duration("soon")


@pytest.mark.parametrize(
    "text,rate", [("off", "0"), ("OFF", "0"), ("", "0"), ("2M", "2M"), ("500K", "500K")]
)
def test_parse_rate(text, rate):
    assert config.parse_rate(text) == rate


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.general.max_concurrent == 3
    assert cfg.general.theme == "aurora"
    assert cfg.general.idle_timeout == 600
    assert cfg.limits.connections == 16
    assert "video" in cfg.categories


def test_default_categories_all_have_icon_and_hue():
    for cat in config.DEFAULT_CATEGORIES.values():
        assert cat.icon
        assert cat.hue.startswith("#")
        assert cat.ext


def test_partial_file_merges_over_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = 9\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 9
    assert cfg.general.theme == "aurora"
    assert cfg.limits.connections == 16


def test_user_category_replaces_default_of_same_name(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[categories.video]\ndir = "~/Vids"\next = ["mkv"]\nicon = "V"\nhue = "#111111"\n'
    )
    cfg = config.load(p)
    assert cfg.categories["video"].dir.name == "Vids"
    assert cfg.categories["video"].ext == ("mkv",)
    assert "iso" in cfg.categories


def test_extensions_are_lowercased_and_stripped_of_dots(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[categories.video]\ndir = "~/V"\next = [".MKV", "Mp4"]\nicon = "V"\nhue = "#111111"\n'
    )
    cfg = config.load(p)
    assert cfg.categories["video"].ext == ("mkv", "mp4")


def test_unknown_keys_are_ignored(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = 4\nwarp_drive = true\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 4


def test_malformed_toml_falls_back_to_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[general\nmax_concurrent = ')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 3
    assert "config.toml" in capsys.readouterr().err


def test_bad_value_type_falls_back_to_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = "lots"\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 3
    assert capsys.readouterr().err


def test_write_default_then_load_roundtrips(tmp_path):
    p = tmp_path / "config.toml"
    config.write_default(p)
    cfg = config.load(p)
    assert cfg.general.theme == "aurora"
    assert cfg.categories["iso"].ext
    assert cfg.domains["huggingface.co"] == "models"


def test_paths_are_expanded(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\ndefault_dir = "~/Elsewhere"\n')
    cfg = config.load(p)
    assert cfg.general.default_dir.is_absolute()
    assert "~" not in str(cfg.general.default_dir)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.config'`

- [ ] **Step 3: Implement**

Create `downloader/dl/config.py`:

```python
import os
import re
import sys
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dl"
STATE_DIR = (
    Path(os.environ["DL_STATE_DIR"])
    if os.environ.get("DL_STATE_DIR")
    else Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "dl"
)
CONFIG_FILE = CONFIG_DIR / "config.toml"

_DURATION = re.compile(r"^(\d+)\s*([smh]?)$")
_MULTIPLIER = {"": 1, "s": 1, "m": 60, "h": 3600}


@dataclass(frozen=True)
class Category:
    name: str
    dir: Path
    ext: tuple[str, ...]
    icon: str
    hue: str


@dataclass(frozen=True)
class General:
    default_dir: Path
    max_concurrent: int
    idle_timeout: int
    theme: str
    ascii_icons: bool
    notify: bool


@dataclass(frozen=True)
class Limits:
    global_rate: str
    per_download: str
    connections: int
    splits: int
    min_split: str


@dataclass(frozen=True)
class Config:
    general: General
    limits: Limits
    categories: dict[str, Category]
    domains: dict[str, str]


def parse_duration(text: str) -> int:
    m = _DURATION.match(str(text).strip().lower())
    if not m:
        raise ValueError(f"bad duration: {text!r}")
    return int(m.group(1)) * _MULTIPLIER[m.group(2)]


def parse_rate(text: str) -> str:
    value = str(text).strip()
    if not value or value.lower() == "off":
        return "0"
    return value


def _expand(p: str) -> Path:
    return Path(p).expanduser().resolve()


def _cat(name: str, directory: str, ext: list[str], icon: str, hue: str) -> Category:
    return Category(
        name=name,
        dir=_expand(directory),
        ext=tuple(e.lower().lstrip(".") for e in ext),
        icon=icon,
        hue=hue,
    )


DEFAULT_CATEGORIES: dict[str, Category] = {
    "video": _cat("video", "~/Movies/Downloads", ["mkv", "mp4", "avi", "mov", "webm", "m4v"], "🎬", "#c678dd"),
    "iso": _cat("iso", "~/Downloads/ISO", ["iso", "img", "dmg", "vhd"], "💿", "#4aa3ff"),
    "archive": _cat("archive", "~/Downloads/Archives", ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz"], "📦", "#e5a44b"),
    "audio": _cat("audio", "~/Music/Downloads", ["mp3", "flac", "wav", "m4a", "ogg", "opus"], "🎵", "#f07eb0"),
    "docs": _cat("docs", "~/Documents/Downloads", ["pdf", "epub", "docx", "xlsx", "pptx", "txt", "md"], "📄", "#4ecdc4"),
    "apps": _cat("apps", "~/Downloads/Apps", ["pkg", "app", "deb", "rpm", "exe", "msi"], "⚙️", "#5ac26a"),
    "models": _cat("models", "~/Downloads/Models", ["safetensors", "gguf", "ckpt", "pt", "pth", "bin"], "🧠", "#9b7bea"),
    "code": _cat("code", "~/Downloads/Code", ["patch", "diff", "whl", "jar"], "💻", "#e58a3c"),
}

DEFAULT_DOMAINS: dict[str, str] = {"huggingface.co": "models", "*.github.com": "code"}

_DEFAULT_GENERAL = General(
    default_dir=_expand("~/Downloads"),
    max_concurrent=3,
    idle_timeout=600,
    theme="aurora",
    ascii_icons=False,
    notify=True,
)

_DEFAULT_LIMITS = Limits(
    global_rate="0", per_download="0", connections=16, splits=16, min_split="1M"
)


def defaults() -> Config:
    return Config(
        general=_DEFAULT_GENERAL,
        limits=_DEFAULT_LIMITS,
        categories=dict(DEFAULT_CATEGORIES),
        domains=dict(DEFAULT_DOMAINS),
    )


def _general_from(raw: dict) -> General:
    g = _DEFAULT_GENERAL
    return replace(
        g,
        default_dir=_expand(raw["default_dir"]) if "default_dir" in raw else g.default_dir,
        max_concurrent=int(raw["max_concurrent"]) if "max_concurrent" in raw else g.max_concurrent,
        idle_timeout=parse_duration(raw["idle_timeout"]) if "idle_timeout" in raw else g.idle_timeout,
        theme=str(raw["theme"]) if "theme" in raw else g.theme,
        ascii_icons=bool(raw["ascii_icons"]) if "ascii_icons" in raw else g.ascii_icons,
        notify=bool(raw["notify"]) if "notify" in raw else g.notify,
    )


def _limits_from(raw: dict) -> Limits:
    lim = _DEFAULT_LIMITS
    return replace(
        lim,
        global_rate=parse_rate(raw["global"]) if "global" in raw else lim.global_rate,
        per_download=parse_rate(raw["per_download"]) if "per_download" in raw else lim.per_download,
        connections=int(raw["connections"]) if "connections" in raw else lim.connections,
        splits=int(raw["splits"]) if "splits" in raw else lim.splits,
        min_split=str(raw["min_split"]) if "min_split" in raw else lim.min_split,
    )


def _categories_from(raw: dict) -> dict[str, Category]:
    cats = dict(DEFAULT_CATEGORIES)
    for name, body in raw.items():
        base = cats.get(name)
        cats[name] = _cat(
            name,
            body.get("dir", str(base.dir) if base else "~/Downloads"),
            list(body.get("ext", list(base.ext) if base else [])),
            body.get("icon", base.icon if base else "📥"),
            body.get("hue", base.hue if base else "#888888"),
        )
    return cats


def load(path: Path | None = None) -> Config:
    target = path or CONFIG_FILE
    if not target.exists():
        return defaults()
    try:
        with open(target, "rb") as fh:
            raw = tomllib.load(fh)
        return Config(
            general=_general_from(raw.get("general", {})),
            limits=_limits_from(raw.get("limits", {})),
            categories=_categories_from(raw.get("categories", {})),
            domains={str(k).lower(): str(v) for k, v in raw.get("domains", DEFAULT_DOMAINS).items()},
        )
    except (tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError, KeyError) as exc:
        print(f"dl: {target} is invalid ({exc}) — using defaults", file=sys.stderr)
        return defaults()


DEFAULT_TOML = """\
[general]
default_dir     = "~/Downloads"
max_concurrent  = 3
idle_timeout    = "10m"
theme           = "aurora"
ascii_icons     = false
notify          = true

[limits]
global       = "off"
per_download = "off"
connections  = 16
splits       = 16
min_split    = "1M"

[categories.video]
dir  = "~/Movies/Downloads"
ext  = ["mkv", "mp4", "avi", "mov", "webm", "m4v"]
icon = "🎬"
hue  = "#c678dd"

[categories.iso]
dir  = "~/Downloads/ISO"
ext  = ["iso", "img", "dmg", "vhd"]
icon = "💿"
hue  = "#4aa3ff"

[categories.archive]
dir  = "~/Downloads/Archives"
ext  = ["zip", "tar", "gz", "bz2", "xz", "7z", "rar", "tgz"]
icon = "📦"
hue  = "#e5a44b"

[categories.audio]
dir  = "~/Music/Downloads"
ext  = ["mp3", "flac", "wav", "m4a", "ogg", "opus"]
icon = "🎵"
hue  = "#f07eb0"

[categories.docs]
dir  = "~/Documents/Downloads"
ext  = ["pdf", "epub", "docx", "xlsx", "pptx", "txt", "md"]
icon = "📄"
hue  = "#4ecdc4"

[categories.apps]
dir  = "~/Downloads/Apps"
ext  = ["pkg", "app", "deb", "rpm", "exe", "msi"]
icon = "⚙️"
hue  = "#5ac26a"

[categories.models]
dir  = "~/Downloads/Models"
ext  = ["safetensors", "gguf", "ckpt", "pt", "pth", "bin"]
icon = "🧠"
hue  = "#9b7bea"

[categories.code]
dir  = "~/Downloads/Code"
ext  = ["patch", "diff", "whl", "jar"]
icon = "💻"
hue  = "#e58a3c"

[domains]
"huggingface.co" = "models"
"*.github.com"   = "code"
"""


def write_default(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(DEFAULT_TOML)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/config.py downloader/tests/test_config.py
git commit -m "dl: add config loading with default fallback"
```

---

## Task 4: Routing

**Files:**
- Create: `downloader/dl/routing.py`
- Test: `downloader/tests/test_routing.py`

**Interfaces:**
- Consumes: `dl.config.Config`, `dl.config.Category`
- Produces:
  - `Resolution(path: Path, category: Category)` — frozen; `path` is the **directory**, not the file
  - `filename_from_url(url: str) -> str`
  - `resolve(url: str, filename: str, cfg: Config, explicit_dir: Path | None = None) -> Resolution`
  - `OTHER: Category` — the fallback category, name `"other"`, icon `"📥"`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_routing.py`:

```python
from pathlib import Path

import pytest

from dl import config, routing


@pytest.fixture
def cfg():
    return config.defaults()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://e.com/ubuntu.iso", "ubuntu.iso"),
        ("https://e.com/a/b/c/file.tar.gz", "file.tar.gz"),
        ("https://e.com/file.zip?token=abc&x=1", "file.zip"),
        ("https://e.com/file.zip#frag", "file.zip"),
        ("https://e.com/My%20Movie.mkv", "My Movie.mkv"),
        ("https://e.com/", ""),
        ("https://e.com", ""),
        ("magnet:?xt=urn:btih:abc", ""),
    ],
)
def test_filename_from_url(url, expected):
    assert routing.filename_from_url(url) == expected


@pytest.mark.parametrize(
    "name,category",
    [
        ("ubuntu.iso", "iso"),
        ("show.mkv", "video"),
        ("SHOW.MKV", "video"),
        ("song.flac", "audio"),
        ("paper.pdf", "docs"),
        ("tool.pkg", "apps"),
        ("model.safetensors", "models"),
        ("data.tar.gz", "archive"),
    ],
)
def test_extension_routing(name, category, cfg):
    r = routing.resolve(f"https://e.com/{name}", name, cfg)
    assert r.category.name == category
    assert r.path == cfg.categories[category].dir


def test_unknown_extension_falls_back(cfg):
    r = routing.resolve("https://e.com/thing.qqq", "thing.qqq", cfg)
    assert r.category.name == "other"
    assert r.path == cfg.general.default_dir


def test_no_extension_falls_back(cfg):
    r = routing.resolve("https://e.com/README", "README", cfg)
    assert r.category.name == "other"


def test_empty_filename_falls_back(cfg):
    r = routing.resolve("https://e.com/", "", cfg)
    assert r.category.name == "other"


def test_domain_exact_match_beats_extension(cfg):
    r = routing.resolve("https://huggingface.co/x/model.zip", "model.zip", cfg)
    assert r.category.name == "models"


def test_domain_wildcard_matches_subdomain(cfg):
    r = routing.resolve("https://api.github.com/x/thing.zip", "thing.zip", cfg)
    assert r.category.name == "code"


def test_domain_wildcard_does_not_match_apex(cfg):
    r = routing.resolve("https://github.com/x/thing.zip", "thing.zip", cfg)
    assert r.category.name == "archive"


def test_domain_match_is_case_insensitive(cfg):
    r = routing.resolve("https://HuggingFace.CO/x/f.zip", "f.zip", cfg)
    assert r.category.name == "models"


def test_domain_match_ignores_port(cfg):
    r = routing.resolve("https://huggingface.co:8443/x/f.zip", "f.zip", cfg)
    assert r.category.name == "models"


def test_domain_pointing_at_unknown_category_falls_back(cfg):
    broken = config.Config(cfg.general, cfg.limits, cfg.categories, {"e.com": "nope"})
    r = routing.resolve("https://e.com/f.zip", "f.zip", broken)
    assert r.category.name == "archive"


def test_explicit_dir_wins_over_everything(cfg):
    r = routing.resolve("https://huggingface.co/m.iso", "m.iso", cfg, explicit_dir=Path("/tmp/x"))
    assert r.path == Path("/tmp/x")
    assert r.category.name == "other"


def test_resolve_is_pure_and_creates_nothing(cfg, tmp_path):
    target = tmp_path / "never"
    routing.resolve("https://e.com/a.iso", "a.iso", cfg, explicit_dir=target)
    assert not target.exists()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.routing'`

- [ ] **Step 3: Implement**

Create `downloader/dl/routing.py`:

```python
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import Category, Config

OTHER = Category(name="other", dir=Path("."), ext=(), icon="📥", hue="#8a8a8a")


@dataclass(frozen=True)
class Resolution:
    path: Path
    category: Category


def filename_from_url(url: str) -> str:
    path = urlsplit(url).path
    if not path:
        return ""
    return unquote(path.rsplit("/", 1)[-1])


def _extension(filename: str) -> str:
    base = filename.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[-1].lower() if "." in base else ""


def _by_domain(url: str, cfg: Config) -> Category | None:
    host = (urlsplit(url).hostname or "").lower()
    if not host:
        return None
    name = cfg.domains.get(host)
    if name is None:
        for pattern, target in cfg.domains.items():
            if pattern.startswith("*.") and host.endswith(pattern[1:]):
                name = target
                break
    return cfg.categories.get(name) if name else None


def _by_extension(filename: str, cfg: Config) -> Category | None:
    ext = _extension(filename)
    if not ext:
        return None
    for category in cfg.categories.values():
        if ext in category.ext:
            return category
    return None


def resolve(
    url: str, filename: str, cfg: Config, explicit_dir: Path | None = None
) -> Resolution:
    if explicit_dir is not None:
        return Resolution(Path(explicit_dir).expanduser(), OTHER)
    name = filename or filename_from_url(url)
    category = _by_domain(url, cfg) or _by_extension(name, cfg)
    if category is None:
        return Resolution(cfg.general.default_dir, OTHER)
    return Resolution(category.dir, category)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/routing.py downloader/tests/test_routing.py
git commit -m "dl: add filetype and domain routing"
```

---

## Task 5: History

**Files:**
- Create: `downloader/dl/history.py`
- Test: `downloader/tests/test_history.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `append(record: dict, path: Path) -> None` — creates parents, fsyncs
  - `tail(path: Path, n: int) -> list[dict]` — oldest-first, skips unparseable lines, returns `[]` for a missing file

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_history.py`:

```python
import json

from dl import history


def test_tail_of_missing_file_is_empty(tmp_path):
    assert history.tail(tmp_path / "none.jsonl", 10) == []


def test_tail_of_empty_file_is_empty(tmp_path):
    p = tmp_path / "h.jsonl"
    p.touch()
    assert history.tail(p, 10) == []


def test_append_then_tail_roundtrips(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "a", "bytes": 1}, p)
    history.append({"name": "b", "bytes": 2}, p)
    assert [r["name"] for r in history.tail(p, 10)] == ["a", "b"]


def test_append_creates_parent_directories(tmp_path):
    p = tmp_path / "deep" / "deeper" / "h.jsonl"
    history.append({"name": "a"}, p)
    assert p.exists()


def test_tail_returns_only_last_n_oldest_first(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(50):
        history.append({"i": i}, p)
    got = history.tail(p, 5)
    assert [r["i"] for r in got] == [45, 46, 47, 48, 49]


def test_tail_skips_truncated_final_line(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"i": 1}, p)
    with open(p, "a") as fh:
        fh.write('{"i": 2, "name": "trunc')
    assert [r["i"] for r in history.tail(p, 10)] == [1]


def test_tail_skips_corrupt_middle_line(tmp_path):
    p = tmp_path / "h.jsonl"
    with open(p, "w") as fh:
        fh.write(json.dumps({"i": 1}) + "\n")
        fh.write("not json at all\n")
        fh.write(json.dumps({"i": 3}) + "\n")
    assert [r["i"] for r in history.tail(p, 10)] == [1, 3]


def test_tail_handles_file_larger_than_one_block(tmp_path):
    p = tmp_path / "h.jsonl"
    for i in range(2000):
        history.append({"i": i, "pad": "x" * 200}, p)
    got = history.tail(p, 3)
    assert [r["i"] for r in got] == [1997, 1998, 1999]


def test_records_are_one_line_each(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"name": "has\nnewline"}, p)
    assert len(p.read_text().splitlines()) == 1


def test_tail_zero_returns_empty(tmp_path):
    p = tmp_path / "h.jsonl"
    history.append({"i": 1}, p)
    assert history.tail(p, 0) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.history'`

- [ ] **Step 3: Implement**

Create `downloader/dl/history.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/history.py downloader/tests/test_history.py
git commit -m "dl: add append-only history log"
```

---

## Task 6: aria2 RPC client

**Files:**
- Create: `downloader/dl/rpc.py`
- Test: `downloader/tests/test_rpc.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `Aria2Error(Exception)` with `.code: int`, `.message: str`
  - `Aria2Unreachable(Exception)`
  - `Aria2(host: str, port: int, secret: str, timeout: float = 5.0)` with methods `get_version`, `add_uri`, `tell_active`, `tell_waiting`, `tell_stopped`, `tell_status`, `pause`, `unpause`, `remove`, `change_position`, `change_global_option`, `change_option`, `get_global_stat`, `shutdown`

All methods return aria2's raw decoded JSON. **Numeric fields remain strings** — callers convert.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_rpc.py`:

```python
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dl.rpc import Aria2, Aria2Error, Aria2Unreachable

SECRET = "s3cr3t"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(body)
        token = body["params"][0] if body["params"] else None
        if token != f"token:{SECRET}":
            payload = {"id": body["id"], "error": {"code": 1, "message": "Unauthorized"}}
        else:
            payload = {"id": body["id"], "result": self.server.replies.get(body["method"], "OK")}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.calls = []
    srv.replies = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
def client(server):
    return Aria2("127.0.0.1", server.server_address[1], SECRET)


def test_get_version(server, client):
    server.replies["aria2.getVersion"] = {"version": "1.37.0"}
    assert client.get_version()["version"] == "1.37.0"


def test_secret_is_sent_as_first_param(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    assert server.calls[0]["params"][0] == f"token:{SECRET}"


def test_jsonrpc_envelope_is_well_formed(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    call = server.calls[0]
    assert call["jsonrpc"] == "2.0"
    assert call["method"] == "aria2.getVersion"
    assert call["id"]


def test_add_uri_passes_uris_and_options(server, client):
    server.replies["aria2.addUri"] = "gid123"
    gid = client.add_uri(["https://e.com/a.iso"], {"dir": "/tmp"})
    assert gid == "gid123"
    assert server.calls[0]["params"][1] == ["https://e.com/a.iso"]
    assert server.calls[0]["params"][2] == {"dir": "/tmp"}


def test_tell_active_sends_no_extra_params(server, client):
    server.replies["aria2.tellActive"] = []
    client.tell_active()
    assert len(server.calls[0]["params"]) == 1


def test_tell_waiting_sends_offset_and_num(server, client):
    server.replies["aria2.tellWaiting"] = []
    client.tell_waiting()
    assert server.calls[0]["params"][1:] == [0, 1000]


def test_tell_stopped_sends_offset_and_num(server, client):
    server.replies["aria2.tellStopped"] = []
    client.tell_stopped()
    assert server.calls[0]["params"][1:] == [0, 1000]


def test_change_position_params(server, client):
    server.replies["aria2.changePosition"] = 2
    assert client.change_position("g1", -1, "POS_CUR") == 2
    assert server.calls[0]["params"][1:] == ["g1", -1, "POS_CUR"]


def test_change_global_option_params(server, client):
    server.replies["aria2.changeGlobalOption"] = "OK"
    client.change_global_option({"max-overall-download-limit": "2M"})
    assert server.calls[0]["params"][1] == {"max-overall-download-limit": "2M"}


def test_pause_unpause_remove_send_gid(server, client):
    for method, name in [
        ("aria2.pause", "pause"),
        ("aria2.unpause", "unpause"),
        ("aria2.remove", "remove"),
    ]:
        server.calls.clear()
        server.replies[method] = "g1"
        getattr(client, name)("g1")
        assert server.calls[0]["params"][1] == "g1"


def test_rpc_fault_raises_aria2error(server, client):
    bad = Aria2("127.0.0.1", server.server_address[1], "wrong-secret")
    with pytest.raises(Aria2Error) as exc:
        bad.get_version()
    assert exc.value.code == 1
    assert "Unauthorized" in exc.value.message


def test_connection_refused_raises_unreachable():
    dead = Aria2("127.0.0.1", 1, SECRET, timeout=0.5)
    with pytest.raises(Aria2Unreachable):
        dead.get_version()


def test_ids_are_unique_across_calls(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    client.get_version()
    assert server.calls[0]["id"] != server.calls[1]["id"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.rpc'`

- [ ] **Step 3: Implement**

Create `downloader/dl/rpc.py`:

```python
import itertools
import json
import urllib.error
import urllib.request


class Aria2Error(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"aria2 error {code}: {message}")
        self.code = code
        self.message = message


class Aria2Unreachable(Exception):
    pass


class Aria2:
    def __init__(self, host: str, port: int, secret: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.secret = secret
        self.timeout = timeout
        self._ids = itertools.count(1)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/jsonrpc"

    def _call(self, method: str, *params):
        payload = {
            "jsonrpc": "2.0",
            "id": f"dl-{next(self._ids)}",
            "method": method,
            "params": [f"token:{self.secret}", *params],
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise Aria2Unreachable(f"HTTP {exc.code} from {self.url}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise Aria2Unreachable(str(exc)) from exc
        if "error" in body:
            err = body["error"]
            raise Aria2Error(int(err.get("code", -1)), str(err.get("message", "")))
        return body.get("result")

    def get_version(self) -> dict:
        return self._call("aria2.getVersion")

    def add_uri(self, uris: list[str], options: dict) -> str:
        return self._call("aria2.addUri", uris, options)

    def tell_active(self) -> list[dict]:
        return self._call("aria2.tellActive")

    def tell_waiting(self, offset: int = 0, num: int = 1000) -> list[dict]:
        return self._call("aria2.tellWaiting", offset, num)

    def tell_stopped(self, offset: int = 0, num: int = 1000) -> list[dict]:
        return self._call("aria2.tellStopped", offset, num)

    def tell_status(self, gid: str) -> dict:
        return self._call("aria2.tellStatus", gid)

    def pause(self, gid: str) -> str:
        return self._call("aria2.pause", gid)

    def unpause(self, gid: str) -> str:
        return self._call("aria2.unpause", gid)

    def remove(self, gid: str) -> str:
        return self._call("aria2.remove", gid)

    def change_position(self, gid: str, pos: int, how: str) -> int:
        return self._call("aria2.changePosition", gid, pos, how)

    def change_option(self, gid: str, options: dict) -> str:
        return self._call("aria2.changeOption", gid, options)

    def change_global_option(self, options: dict) -> str:
        return self._call("aria2.changeGlobalOption", options)

    def get_global_stat(self) -> dict:
        return self._call("aria2.getGlobalStat")

    def shutdown(self) -> str:
        return self._call("aria2.shutdown")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/rpc.py downloader/tests/test_rpc.py
git commit -m "dl: add aria2 json-rpc client"
```

---

## Task 7: Daemon lifecycle

**Files:**
- Create: `downloader/dl/daemon.py`
- Test: `downloader/tests/test_daemon.py`

**Interfaces:**
- Consumes: `dl.config` (`Config`, `STATE_DIR`), `dl.rpc` (`Aria2`, `Aria2Error`, `Aria2Unreachable`)
- Produces:
  - `Aria2Missing(Exception)`, `DaemonStartFailed(Exception)`
  - `read_secret(state: Path) -> str` — creates a 0600 file on first call
  - `read_port(state: Path) -> int`, `write_port(state: Path, port: int) -> None`
  - `write_hook_shims(state: Path, python: str) -> tuple[Path, Path]` — returns `(complete_sh, error_sh)`, both 0755
  - `aria2_args(cfg: Config, state: Path, port: int, secret: str) -> list[str]`
  - `bump_generation(state: Path) -> int`, `read_generation(state: Path) -> int`
  - `ensure_running(cfg: Config, state: Path = STATE_DIR) -> Aria2`
  - `PORT_RANGE: range` — `range(6810, 6820)`

`ensure_running` may spawn a process; every other function in this module is filesystem-only and directly testable.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_daemon.py`:

```python
import stat

import pytest

from dl import config, daemon


@pytest.fixture
def cfg():
    return config.defaults()


def test_read_secret_creates_file_with_0600(tmp_path):
    secret = daemon.read_secret(tmp_path)
    target = tmp_path / "rpc.secret"
    assert len(secret) >= 32
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_read_secret_is_stable_across_calls(tmp_path):
    assert daemon.read_secret(tmp_path) == daemon.read_secret(tmp_path)


def test_read_port_defaults_to_first_in_range(tmp_path):
    assert daemon.read_port(tmp_path) == daemon.PORT_RANGE.start


def test_write_then_read_port(tmp_path):
    daemon.write_port(tmp_path, 6815)
    assert daemon.read_port(tmp_path) == 6815


def test_read_port_ignores_garbage(tmp_path):
    (tmp_path / "port").write_text("not-a-port")
    assert daemon.read_port(tmp_path) == daemon.PORT_RANGE.start


def test_hook_shims_are_executable_and_exec_the_venv(tmp_path):
    complete, error = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    for path, mode in [(complete, "complete"), (error, "error")]:
        body = path.read_text()
        assert body.startswith("#!/bin/sh")
        assert "/opt/venv/bin/python" in body
        assert f"-m dl.hook {mode}" in body
        assert '"$@"' in body
        assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_hook_shims_pin_the_state_dir_so_a_fresh_hook_process_agrees(tmp_path):
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    assert f"DL_STATE_DIR={tmp_path}" in complete.read_text()


def test_hook_shims_are_rewritten_when_python_moves(tmp_path):
    daemon.write_hook_shims(tmp_path, "/old/python")
    complete, _ = daemon.write_hook_shims(tmp_path, "/new/python")
    assert "/new/python" in complete.read_text()
    assert "/old/python" not in complete.read_text()


def test_generation_starts_at_zero_and_increments(tmp_path):
    assert daemon.read_generation(tmp_path) == 0
    assert daemon.bump_generation(tmp_path) == 1
    assert daemon.bump_generation(tmp_path) == 2
    assert daemon.read_generation(tmp_path) == 2


def test_generation_ignores_corrupt_file(tmp_path):
    (tmp_path / "generation").write_text("garbage")
    assert daemon.read_generation(tmp_path) == 0


def test_aria2_args_include_rpc_secret_and_localhost(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "abc")
    assert "--enable-rpc" in args
    assert "--rpc-secret=abc" in args
    assert "--rpc-listen-port=6810" in args
    assert "--rpc-listen-all=false" in args


def test_aria2_args_never_use_stop_with_process(tmp_path, cfg):
    assert not any("stop-with-process" in a for a in daemon.aria2_args(cfg, tmp_path, 6810, "x"))


def test_aria2_args_apply_config_limits(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "x")
    assert "--max-concurrent-downloads=3" in args
    assert "--max-connection-per-server=16" in args
    assert "--split=16" in args
    assert "--min-split-size=1M" in args
    assert "--max-overall-download-limit=0" in args


def test_aria2_args_set_session_and_hooks(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "x")
    assert f"--save-session={tmp_path / 'session'}" in args
    assert any(a.startswith("--on-download-complete=") for a in args)
    assert any(a.startswith("--on-download-error=") for a in args)
    assert "--auto-file-renaming=true" in args
    assert "--allow-overwrite=false" in args


def test_aria2_args_restore_session_only_when_present(tmp_path, cfg):
    assert not any(a.startswith("--input-file=") for a in daemon.aria2_args(cfg, tmp_path, 6810, "x"))
    (tmp_path / "session").write_text("")
    assert f"--input-file={tmp_path / 'session'}" in daemon.aria2_args(cfg, tmp_path, 6810, "x")


def test_ensure_running_without_binary_raises(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda _: None)
    with pytest.raises(daemon.Aria2Missing):
        daemon.ensure_running(cfg, tmp_path)


def test_corrupt_session_is_quarantined(tmp_path):
    session = tmp_path / "session"
    session.write_text("junk")
    daemon.quarantine_session(tmp_path)
    assert not session.exists()
    assert (tmp_path / "session.bad").read_text() == "junk"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.daemon'`

- [ ] **Step 3: Implement**

Create `downloader/dl/daemon.py`:

```python
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .config import STATE_DIR, Config
from .rpc import Aria2, Aria2Error, Aria2Unreachable

PORT_RANGE = range(6810, 6820)
_SHIM = '#!/bin/sh\nexec env DL_STATE_DIR={state} {python} -m dl.hook {mode} "$@"\n'


class Aria2Missing(Exception):
    pass


class DaemonStartFailed(Exception):
    pass


def read_secret(state: Path) -> str:
    state.mkdir(parents=True, exist_ok=True)
    target = state / "rpc.secret"
    if not target.exists():
        target.write_text(secrets.token_urlsafe(32))
        target.chmod(0o600)
    return target.read_text().strip()


def read_port(state: Path) -> int:
    target = state / "port"
    try:
        return int(target.read_text().strip())
    except (OSError, ValueError):
        return PORT_RANGE.start


def write_port(state: Path, port: int) -> None:
    state.mkdir(parents=True, exist_ok=True)
    (state / "port").write_text(str(port))


def write_hook_shims(state: Path, python: str) -> tuple[Path, Path]:
    hooks = state / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    written = []
    for mode in ("complete", "error"):
        target = hooks / f"{mode}.sh"
        target.write_text(_SHIM.format(python=python, mode=mode, state=state))
        target.chmod(0o755)
        written.append(target)
    return written[0], written[1]


def read_generation(state: Path) -> int:
    try:
        return int((state / "generation").read_text().strip())
    except (OSError, ValueError):
        return 0


def bump_generation(state: Path) -> int:
    state.mkdir(parents=True, exist_ok=True)
    value = read_generation(state) + 1
    (state / "generation").write_text(str(value))
    return value


def quarantine_session(state: Path) -> None:
    session = state / "session"
    if session.exists():
        session.replace(state / "session.bad")


def aria2_args(cfg: Config, state: Path, port: int, secret: str) -> list[str]:
    complete, error = write_hook_shims(state, sys.executable)
    args = [
        "aria2c",
        "--enable-rpc",
        "--rpc-listen-all=false",
        f"--rpc-listen-port={port}",
        f"--rpc-secret={secret}",
        "--continue=true",
        "--auto-file-renaming=true",
        "--allow-overwrite=false",
        "--max-tries=5",
        "--retry-wait=3",
        "--daemon=false",
        f"--max-concurrent-downloads={cfg.general.max_concurrent}",
        f"--max-connection-per-server={cfg.limits.connections}",
        f"--split={cfg.limits.splits}",
        f"--min-split-size={cfg.limits.min_split}",
        f"--max-overall-download-limit={cfg.limits.global_rate}",
        f"--max-download-limit={cfg.limits.per_download}",
        f"--save-session={state / 'session'}",
        "--save-session-interval=30",
        "--force-save=true",
        f"--on-download-complete={complete}",
        f"--on-download-error={error}",
        f"--log={state / 'aria2.log'}",
        "--log-level=error",
    ]
    session = state / "session"
    if session.exists():
        args.append(f"--input-file={session}")
    return args


def _probe(port: int, secret: str) -> str:
    client = Aria2("127.0.0.1", port, secret, timeout=1.0)
    try:
        client.get_version()
        return "ours"
    except Aria2Error:
        return "foreign"
    except Aria2Unreachable:
        return "free"


def _spawn(cfg: Config, state: Path, port: int, secret: str) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "spawn.log", "wb") as log:
        subprocess.Popen(
            aria2_args(cfg, state, port, secret),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=os.path.expanduser("~"),
        )


def _tail_log(state: Path, lines: int = 20) -> str:
    for name in ("aria2.log", "spawn.log"):
        target = state / name
        if target.exists():
            body = target.read_text(errors="replace").splitlines()[-lines:]
            if body:
                return "\n".join(body)
    return "(no log output)"


def ensure_running(cfg: Config, state: Path = STATE_DIR) -> Aria2:
    if shutil.which("aria2c") is None:
        raise Aria2Missing("aria2c not found — brew install aria2")

    secret = read_secret(state)
    preferred = read_port(state)
    candidates = [preferred] + [p for p in PORT_RANGE if p != preferred]

    free_port = None
    for port in candidates:
        status = _probe(port, secret)
        if status == "ours":
            write_port(state, port)
            return Aria2("127.0.0.1", port, secret)
        if status == "free" and free_port is None:
            free_port = port

    if free_port is None:
        raise DaemonStartFailed(f"no free port in {PORT_RANGE.start}-{PORT_RANGE.stop - 1}")

    _spawn(cfg, state, free_port, secret)
    client = Aria2("127.0.0.1", free_port, secret, timeout=1.0)
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            client.get_version()
            write_port(state, free_port)
            return Aria2("127.0.0.1", free_port, secret)
        except (Aria2Unreachable, Aria2Error):
            time.sleep(0.1)

    quarantine_session(state)
    raise DaemonStartFailed(f"aria2c did not answer within 5s\n{_tail_log(state)}")
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/daemon.py downloader/tests/test_daemon.py
git commit -m "dl: add daemon lifecycle, port selection, hook shims"
```

---

## Task 8: Completion hook

**Files:**
- Create: `downloader/dl/hook.py`
- Test: `downloader/tests/test_hook.py`

**Interfaces:**
- Consumes: `dl.config`, `dl.daemon`, `dl.history`, `dl.routing`, `dl.rpc`
- Produces:
  - `build_record(status: dict, mode: str, cfg: Config) -> dict` — the history row
  - `relocate(path: Path, cfg: Config, url: str) -> Path` — moves the file when the real filename routes elsewhere; returns the final path
  - `notify(title: str, body: str) -> None`
  - `arm_idle_shutdown(client, cfg, state) -> bool` — returns whether a sleeper was armed
  - `main(argv: list[str]) -> int`

`main` never raises: any exception is logged to `state/hook.log` and returns 0, because a crashing hook must not disturb aria2.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_hook.py`:

```python
import json
from pathlib import Path

import pytest

from dl import config, hook


@pytest.fixture
def cfg():
    return config.defaults()


def status(**over):
    base = {
        "gid": "g1",
        "status": "complete",
        "totalLength": "1048576",
        "completedLength": "1048576",
        "downloadSpeed": "0",
        "files": [{"path": "/tmp/a.iso", "uris": [{"uri": "https://e.com/a.iso"}]}],
        "errorCode": "0",
        "errorMessage": "",
    }
    base.update(over)
    return base


def test_build_record_converts_string_numbers(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["bytes"] == 1048576
    assert isinstance(rec["bytes"], int)
    assert rec["status"] == "ok"


def test_build_record_captures_name_url_and_category(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["name"] == "a.iso"
    assert rec["url"] == "https://e.com/a.iso"
    assert rec["category"] == "iso"


def test_build_record_for_error_carries_message(cfg):
    rec = hook.build_record(
        status(status="error", errorCode="22", errorMessage="HTTP 403"), "error", cfg
    )
    assert rec["status"] == "error"
    assert rec["error"] == "HTTP 403"


def test_build_record_handles_missing_files_list(cfg):
    rec = hook.build_record(status(files=[]), "complete", cfg)
    assert rec["name"] == ""
    assert rec["url"] == ""


def test_build_record_avg_bps_is_zero_when_instant(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["avg_bps"] >= 0


def test_relocate_moves_file_when_category_changes(tmp_path, cfg):
    src_dir = tmp_path / "wrong"
    src_dir.mkdir()
    src = src_dir / "movie.mkv"
    src.write_text("data")
    target_dir = tmp_path / "right"
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert moved == target_dir / "movie.mkv"
    assert moved.read_text() == "data"
    assert not src.exists()


def test_relocate_is_a_noop_when_already_correct(tmp_path, cfg):
    target_dir = tmp_path / "vids"
    target_dir.mkdir()
    src = target_dir / "movie.mkv"
    src.write_text("data")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert moved == src
    assert src.exists()


def test_relocate_does_not_clobber_existing_file(tmp_path, cfg):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    src = src_dir / "movie.mkv"
    src.write_text("new")
    target_dir = tmp_path / "b"
    target_dir.mkdir()
    (target_dir / "movie.mkv").write_text("existing")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert (target_dir / "movie.mkv").read_text() == "existing"
    assert moved.read_text() == "new"
    assert moved != target_dir / "movie.mkv"


def test_relocate_returns_original_when_file_is_missing(tmp_path, cfg):
    ghost = tmp_path / "ghost.mkv"
    assert hook.relocate(ghost, cfg, "https://e.com/ghost.mkv") == ghost


class FakeClient:
    def __init__(self, active=(), waiting=()):
        self._active = list(active)
        self._waiting = list(waiting)
        self.shutdown_called = False

    def tell_active(self):
        return self._active

    def tell_waiting(self, offset=0, num=1000):
        return self._waiting

    def shutdown(self):
        self.shutdown_called = True
        return "OK"


def test_arm_idle_shutdown_skips_when_queue_is_busy(tmp_path, cfg):
    assert hook.arm_idle_shutdown(FakeClient(active=[{"gid": "g"}]), cfg, tmp_path) is False


def test_arm_idle_shutdown_arms_when_queue_is_empty(tmp_path, cfg, monkeypatch):
    spawned = []
    monkeypatch.setattr(hook, "_spawn_sleeper", lambda *a: spawned.append(a))
    assert hook.arm_idle_shutdown(FakeClient(), cfg, tmp_path) is True
    assert spawned


def test_main_appends_history_and_survives_rpc_failure(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: cfg)

    def boom(*_a, **_k):
        raise RuntimeError("no daemon")

    monkeypatch.setattr(hook.daemon, "ensure_running", boom)
    assert hook.main(["complete", "g1", "1", str(tmp_path / "a.iso")]) == 0
    assert (tmp_path / "hook.log").exists()


def test_main_writes_history_row_on_success(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(hook, "notify", lambda *a: None)
    monkeypatch.setattr(hook, "arm_idle_shutdown", lambda *a: False)

    client = FakeClient()
    client.tell_status = lambda gid: status()
    monkeypatch.setattr(hook.daemon, "ensure_running", lambda *a, **k: client)

    assert hook.main(["complete", "g1", "1", "/tmp/a.iso"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert rows[0]["name"] == "a.iso"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.hook'`

- [ ] **Step 3: Implement**

Create `downloader/dl/hook.py`:

```python
import subprocess
import sys
import time
import traceback
from pathlib import Path

from . import config, daemon, history, routing
from .config import STATE_DIR, Config
from .rpc import Aria2


def _first_file(status: dict) -> dict:
    files = status.get("files") or []
    return files[0] if files else {}


def _first_uri(status: dict) -> str:
    uris = _first_file(status).get("uris") or []
    return uris[0].get("uri", "") if uris else ""


def build_record(status: dict, mode: str, cfg: Config) -> dict:
    path = Path(_first_file(status).get("path", ""))
    url = _first_uri(status)
    name = path.name
    total = int(status.get("totalLength", 0) or 0)
    speed = int(status.get("downloadSpeed", 0) or 0)
    resolution = routing.resolve(url, name, cfg)
    record = {
        "ts": int(time.time()),
        "name": name,
        "bytes": total,
        "seconds": 0,
        "avg_bps": max(speed, 0),
        "path": str(path),
        "category": resolution.category.name,
        "url": url,
        "status": "ok" if mode == "complete" else "error",
    }
    if mode != "complete":
        record["error"] = status.get("errorMessage") or f"code {status.get('errorCode', '?')}"
    return record


def relocate(path: Path, cfg: Config, url: str) -> Path:
    if not path.exists():
        return path
    target_dir = routing.resolve(url, path.name, cfg).path
    if target_dir == path.parent:
        return path
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / path.name
    if destination.exists():
        stem, suffix = destination.stem, destination.suffix
        n = 2
        while destination.exists():
            destination = target_dir / f"{stem}.{n}{suffix}"
            n += 1
    path.replace(destination)
    return destination


def notify(title: str, body: str) -> None:
    script = f'display notification {body!r} with title {title!r}'
    subprocess.run(["osascript", "-e", script], capture_output=True, check=False)


def _spawn_sleeper(state: Path, generation: int, delay: int) -> None:
    subprocess.Popen(
        [sys.executable, "-m", "dl.hook", "idle", str(generation), str(delay), str(state)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def arm_idle_shutdown(client, cfg: Config, state: Path) -> bool:
    if client.tell_active() or client.tell_waiting():
        return False
    generation = daemon.bump_generation(state)
    _spawn_sleeper(state, generation, cfg.general.idle_timeout)
    return True


def _run_idle(generation: int, delay: int, state: Path) -> int:
    time.sleep(delay)
    if daemon.read_generation(state) != generation:
        return 0
    try:
        client = Aria2("127.0.0.1", daemon.read_port(state), daemon.read_secret(state), timeout=2.0)
        if not client.tell_active() and not client.tell_waiting():
            client.shutdown()
    except Exception:
        pass
    return 0


def _log_failure(state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    with open(state / "hook.log", "a") as fh:
        fh.write(f"--- {time.ctime()}\n{traceback.format_exc()}\n")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    state = STATE_DIR
    if not args:
        return 0
    if args[0] == "idle":
        return _run_idle(int(args[1]), int(args[2]), Path(args[3]))

    try:
        mode = args[0]
        gid = args[1] if len(args) > 1 else ""
        cfg = config.load()
        client = daemon.ensure_running(cfg, state)
        status = client.tell_status(gid)
        record = build_record(status, mode, cfg)
        if mode == "complete" and record["path"]:
            record["path"] = str(relocate(Path(record["path"]), cfg, record["url"]))
        history.append(record, state / "history.jsonl")
        if cfg.general.notify:
            title = "Download complete" if mode == "complete" else "Download failed"
            notify(title, record["name"] or gid)
        arm_idle_shutdown(client, cfg, state)
    except Exception:
        _log_failure(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/hook.py downloader/tests/test_hook.py
git commit -m "dl: add completion hook with history, notify, idle shutdown"
```

---

## Task 9: CLI subcommands and dispatch

**Files:**
- Create: `downloader/dl/cli.py`
- Create: `downloader/dl/__main__.py`
- Test: `downloader/tests/test_cli.py`

**Interfaces:**
- Consumes: `dl.config`, `dl.daemon`, `dl.format`, `dl.routing`, `dl.rpc`
- Produces:
  - `add_options(cfg: Config, resolution: Resolution) -> dict` — the aria2 option dict for `addUri`
  - `cmd_add(urls: list[str], cfg, client, explicit_dir: Path | None) -> int`
  - `cmd_ls(cfg, client, use_color: bool) -> int`
  - `cmd_pause(target: str, client) -> int`, `cmd_resume(target: str, client) -> int`
  - `cmd_rm(target: str, client) -> int`
  - `cmd_limit(rate: str, cfg, client) -> int`
  - `cmd_kill(client) -> int`
  - `read_url_file(source: str) -> list[str]` — `"-"` means stdin
  - `main(argv: list[str] | None = None) -> int` in `__main__.py`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_cli.py`:

```python
import io
from pathlib import Path

import pytest

from dl import cli, config, routing


@pytest.fixture
def cfg():
    return config.defaults()


class FakeClient:
    def __init__(self):
        self.added = []
        self.paused = []
        self.unpaused = []
        self.removed = []
        self.global_options = {}
        self.shutdown_called = False
        self.active = []
        self.waiting = []

    def add_uri(self, uris, options):
        self.added.append((uris, options))
        return f"gid{len(self.added)}"

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return self.waiting

    def tell_stopped(self, offset=0, num=1000):
        return []

    def pause(self, gid):
        self.paused.append(gid)
        return gid

    def unpause(self, gid):
        self.unpaused.append(gid)
        return gid

    def remove(self, gid):
        self.removed.append(gid)
        return gid

    def change_global_option(self, options):
        self.global_options.update(options)
        return "OK"

    def shutdown(self):
        self.shutdown_called = True
        return "OK"


def test_add_options_carry_dir_and_limits(cfg):
    resolution = routing.resolve("https://e.com/a.iso", "a.iso", cfg)
    options = cli.add_options(cfg, resolution)
    assert options["dir"] == str(cfg.categories["iso"].dir)
    assert options["max-connection-per-server"] == "16"
    assert options["split"] == "16"
    assert options["min-split-size"] == "1M"


def test_cmd_add_queues_each_url(cfg, capsys):
    client = FakeClient()
    assert cli.cmd_add(["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None) == 0
    assert len(client.added) == 2
    out = capsys.readouterr().out
    assert "a.iso" in out and "b.mkv" in out


def test_cmd_add_routes_by_extension(cfg):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, None)
    assert client.added[0][1]["dir"] == str(cfg.categories["iso"].dir)


def test_cmd_add_honours_explicit_dir(cfg, tmp_path):
    client = FakeClient()
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, tmp_path)
    assert client.added[0][1]["dir"] == str(tmp_path)


def test_cmd_add_creates_destination_directory(cfg, tmp_path):
    client = FakeClient()
    target = tmp_path / "made" / "here"
    cli.cmd_add(["https://e.com/a.iso"], cfg, client, target)
    assert target.is_dir()


def test_cmd_add_rejects_unwritable_destination(cfg, tmp_path, capsys):
    client = FakeClient()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        rc = cli.cmd_add(["https://e.com/a.iso"], cfg, client, locked / "sub")
        assert rc == 1
        assert "cannot write" in capsys.readouterr().err
        assert not client.added
    finally:
        locked.chmod(0o700)


def test_cmd_add_with_no_urls_is_an_error(cfg, capsys):
    assert cli.cmd_add([], cfg, FakeClient(), None) == 1
    assert capsys.readouterr().err


def test_cmd_ls_lists_active_and_waiting(cfg, capsys):
    client = FakeClient()
    client.active = [
        {"gid": "g1", "status": "active", "totalLength": "1000", "completedLength": "500",
         "downloadSpeed": "100", "files": [{"path": "/tmp/a.iso", "uris": []}]}
    ]
    client.waiting = [
        {"gid": "g2", "status": "waiting", "totalLength": "0", "completedLength": "0",
         "downloadSpeed": "0", "files": [{"path": "/tmp/b.mkv", "uris": []}]}
    ]
    assert cli.cmd_ls(cfg, client, use_color=False) == 0
    out = capsys.readouterr().out
    assert "g1" in out and "a.iso" in out and "50%" in out
    assert "g2" in out and "b.mkv" in out


def test_cmd_ls_emits_no_escape_codes_without_color(cfg, capsys):
    client = FakeClient()
    client.active = [
        {"gid": "g1", "status": "active", "totalLength": "100", "completedLength": "1",
         "downloadSpeed": "1", "files": [{"path": "/tmp/a.iso", "uris": []}]}
    ]
    cli.cmd_ls(cfg, client, use_color=False)
    assert "\x1b[" not in capsys.readouterr().out


def test_cmd_pause_single_gid():
    client = FakeClient()
    assert cli.cmd_pause("g1", client) == 0
    assert client.paused == ["g1"]


def test_cmd_pause_all_pauses_every_active():
    client = FakeClient()
    client.active = [{"gid": "g1"}, {"gid": "g2"}]
    cli.cmd_pause("all", client)
    assert client.paused == ["g1", "g2"]


def test_cmd_resume_all_unpauses_every_waiting():
    client = FakeClient()
    client.waiting = [{"gid": "g3"}]
    cli.cmd_resume("all", client)
    assert client.unpaused == ["g3"]


def test_cmd_rm_removes_gid():
    client = FakeClient()
    assert cli.cmd_rm("g9", client) == 0
    assert client.removed == ["g9"]


def test_cmd_limit_sets_overall_rate(cfg):
    client = FakeClient()
    assert cli.cmd_limit("2M", cfg, client) == 0
    assert client.global_options["max-overall-download-limit"] == "2M"


def test_cmd_limit_off_means_zero(cfg):
    client = FakeClient()
    cli.cmd_limit("off", cfg, client)
    assert client.global_options["max-overall-download-limit"] == "0"


def test_cmd_kill_calls_shutdown():
    client = FakeClient()
    assert cli.cmd_kill(client) == 0
    assert client.shutdown_called


def test_read_url_file_from_disk(tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("https://e.com/a.iso\n\n# comment\nhttps://e.com/b.mkv\n")
    assert cli.read_url_file(str(p)) == ["https://e.com/a.iso", "https://e.com/b.mkv"]


def test_read_url_file_from_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("https://e.com/a.iso\n"))
    assert cli.read_url_file("-") == ["https://e.com/a.iso"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.cli'`

- [ ] **Step 3: Implement the CLI**

Create `downloader/dl/cli.py`:

```python
import os
import sys
from pathlib import Path

from . import routing
from .config import Config, parse_rate
from .format import human_bytes, human_speed
from .routing import Resolution


def add_options(cfg: Config, resolution: Resolution) -> dict:
    return {
        "dir": str(resolution.path),
        "max-connection-per-server": str(cfg.limits.connections),
        "split": str(cfg.limits.splits),
        "min-split-size": cfg.limits.min_split,
        "max-download-limit": cfg.limits.per_download,
    }


def _ensure_writable(target: Path) -> bool:
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    return os.access(target, os.W_OK)


def cmd_add(urls: list[str], cfg: Config, client, explicit_dir: Path | None) -> int:
    if not urls:
        print("dl: no URLs given", file=sys.stderr)
        return 1
    failures = 0
    for url in urls:
        name = routing.filename_from_url(url)
        resolution = routing.resolve(url, name, cfg, explicit_dir)
        if not _ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        client.add_uri([url], add_options(cfg, resolution))
        print(f"  {resolution.category.icon} queued  {name or url}  →  {resolution.path}")
    return 1 if failures else 0


def _rows(client) -> list[dict]:
    return list(client.tell_active()) + list(client.tell_waiting()) + list(client.tell_stopped())


def cmd_ls(cfg: Config, client, use_color: bool) -> int:
    for item in _rows(client):
        total = int(item.get("totalLength", 0) or 0)
        done = int(item.get("completedLength", 0) or 0)
        pct = int(done * 100 / total) if total else 0
        files = item.get("files") or [{}]
        name = Path(files[0].get("path", "")).name or "(pending)"
        print(
            f"{item.get('gid', ''):<18} {item.get('status', ''):<9} {pct:>3}% "
            f"{human_bytes(total):>10} {human_speed(int(item.get('downloadSpeed', 0) or 0)):>12}  {name}"
        )
    return 0


def _gids(client, source: str) -> list[str]:
    if source == "active":
        return [i["gid"] for i in client.tell_active()]
    return [i["gid"] for i in client.tell_waiting()]


def cmd_pause(target: str, client) -> int:
    gids = _gids(client, "active") if target == "all" else [target]
    for gid in gids:
        client.pause(gid)
    return 0


def cmd_resume(target: str, client) -> int:
    gids = _gids(client, "waiting") if target == "all" else [target]
    for gid in gids:
        client.unpause(gid)
    return 0


def cmd_rm(target: str, client) -> int:
    client.remove(target)
    return 0


def cmd_limit(rate: str, cfg: Config, client) -> int:
    value = parse_rate(rate)
    client.change_global_option({"max-overall-download-limit": value})
    print(f"  limit {'off' if value == '0' else value}")
    return 0


def cmd_kill(client) -> int:
    client.shutdown()
    print("  daemon stopped")
    return 0


def read_url_file(source: str) -> list[str]:
    text = sys.stdin.read() if source == "-" else Path(source).read_text()
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
```

- [ ] **Step 4: Implement dispatch**

Create `downloader/dl/__main__.py`:

```python
import sys
from pathlib import Path

from . import cli, config, daemon
from .config import CONFIG_FILE

USAGE = """\
dl — download manager

  dl <url> [url...]        queue downloads
  dl -f <file|->           queue URLs from a file or stdin
  dl -d <dir> <url>        override the destination for this download
  dl                       open the TUI

  dl ls                    list downloads
  dl pause <gid|all>       dl resume <gid|all>      dl rm <gid>
  dl limit <rate|off>      global speed limit
  dl watch                 queue URLs as you copy them
  dl kill                  stop the daemon
"""

SUBCOMMANDS = {"ls", "pause", "resume", "rm", "limit", "watch", "kill", "help"}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    if not CONFIG_FILE.exists():
        config.write_default(CONFIG_FILE)
    cfg = config.load(CONFIG_FILE)

    explicit_dir: Path | None = None
    if args and args[0] == "-d":
        if len(args) < 2:
            print("dl: -d needs a directory", file=sys.stderr)
            return 1
        explicit_dir = Path(args[1]).expanduser()
        args = args[2:]

    urls: list[str] = []
    if args and args[0] == "-f":
        if len(args) < 2:
            print("dl: -f needs a file or -", file=sys.stderr)
            return 1
        urls = cli.read_url_file(args[1])
        args = args[2:]

    command = args[0] if args and args[0] in SUBCOMMANDS else None
    if command is None:
        urls += [a for a in args if not a.startswith("-")]

    try:
        client = daemon.ensure_running(cfg)
    except daemon.Aria2Missing as exc:
        print(f"dl: {exc}", file=sys.stderr)
        return 1
    except daemon.DaemonStartFailed as exc:
        print(f"dl: {exc}", file=sys.stderr)
        return 1

    if command == "ls":
        return cli.cmd_ls(cfg, client, use_color=sys.stdout.isatty())
    if command == "pause":
        return cli.cmd_pause(args[1] if len(args) > 1 else "all", client)
    if command == "resume":
        return cli.cmd_resume(args[1] if len(args) > 1 else "all", client)
    if command == "rm":
        if len(args) < 2:
            print("dl: rm needs a gid", file=sys.stderr)
            return 1
        return cli.cmd_rm(args[1], client)
    if command == "limit":
        return cli.cmd_limit(args[1] if len(args) > 1 else "off", cfg, client)
    if command == "kill":
        return cli.cmd_kill(client)
    if command == "watch":
        from . import watch

        return watch.run(cfg, client)

    if urls:
        daemon.bump_generation(config.STATE_DIR)
        return cli.cmd_add(urls, cfg, client, explicit_dir)

    if not sys.stdout.isatty():
        print("dl: not a terminal — try `dl ls`", file=sys.stderr)
        return 1

    from .tui.app import run_tui

    return run_tui(cfg, client)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add downloader/dl/cli.py downloader/dl/__main__.py downloader/tests/test_cli.py
git commit -m "dl: add cli subcommands and argv dispatch"
```

---

## Task 10: Clipboard watcher

**Files:**
- Create: `downloader/dl/watch.py`
- Test: `downloader/tests/test_watch.py`

**Interfaces:**
- Consumes: `dl.cli`, `dl.config`
- Produces:
  - `is_downloadable(text: str) -> bool`
  - `poll_once(text: str, seen: deque, cfg, client) -> bool` — returns whether it queued
  - `run(cfg, client, interval: float = 0.8, reader=None, iterations: int | None = None) -> int`

`reader` and `iterations` exist purely so tests drive the loop without `pbpaste` or a real clock.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_watch.py`:

```python
from collections import deque

import pytest

from dl import config, watch


@pytest.fixture
def cfg():
    return config.defaults()


class FakeClient:
    def __init__(self):
        self.added = []

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        return "gid"


@pytest.mark.parametrize(
    "text,ok",
    [
        ("https://e.com/a.iso", True),
        ("http://e.com/a.iso", True),
        ("magnet:?xt=urn:btih:abc", True),
        ("ftp://e.com/a.iso", True),
        ("just some text", False),
        ("", False),
        ("   ", False),
        ("https://e.com/a.iso and more words", False),
        ("file:///etc/passwd", False),
    ],
)
def test_is_downloadable(text, ok):
    assert watch.is_downloadable(text) is ok


def test_poll_once_queues_a_new_url(cfg):
    client = FakeClient()
    assert watch.poll_once("https://e.com/a.iso", deque(maxlen=20), cfg, client) is True
    assert client.added == ["https://e.com/a.iso"]


def test_poll_once_ignores_repeat_of_same_url(cfg):
    client = FakeClient()
    seen = deque(maxlen=20)
    watch.poll_once("https://e.com/a.iso", seen, cfg, client)
    assert watch.poll_once("https://e.com/a.iso", seen, cfg, client) is False
    assert len(client.added) == 1


def test_poll_once_ignores_non_urls(cfg):
    client = FakeClient()
    assert watch.poll_once("hello", deque(maxlen=20), cfg, client) is False
    assert not client.added


def test_seen_ring_forgets_beyond_twenty(cfg):
    client = FakeClient()
    seen = deque(maxlen=20)
    watch.poll_once("https://e.com/first.iso", seen, cfg, client)
    for i in range(20):
        watch.poll_once(f"https://e.com/{i}.iso", seen, cfg, client)
    assert watch.poll_once("https://e.com/first.iso", seen, cfg, client) is True


def test_run_drives_the_reader_for_n_iterations(cfg):
    client = FakeClient()
    clips = iter(["https://e.com/a.iso", "https://e.com/a.iso", "https://e.com/b.mkv"])
    watch.run(cfg, client, interval=0, reader=lambda: next(clips, ""), iterations=3)
    assert client.added == ["https://e.com/a.iso", "https://e.com/b.mkv"]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.watch'`

- [ ] **Step 3: Implement**

Create `downloader/dl/watch.py`:

```python
import subprocess
import time
from collections import deque

from . import cli, routing
from .config import Config

SCHEMES = ("http://", "https://", "ftp://", "magnet:")


def is_downloadable(text: str) -> bool:
    value = text.strip()
    if not value or len(value.split()) != 1:
        return False
    return value.startswith(SCHEMES)


def read_clipboard() -> str:
    try:
        return subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False, timeout=2
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def poll_once(text: str, seen: deque, cfg: Config, client) -> bool:
    value = text.strip()
    if not is_downloadable(value) or value in seen:
        return False
    seen.append(value)
    name = routing.filename_from_url(value)
    resolution = routing.resolve(value, name, cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    client.add_uri([value], cli.add_options(cfg, resolution))
    print(f"  {resolution.category.icon} caught  {name or value}  →  {resolution.path}")
    return True


def run(
    cfg: Config,
    client,
    interval: float = 0.8,
    reader=None,
    iterations: int | None = None,
) -> int:
    source = reader or read_clipboard
    seen: deque = deque(maxlen=20)
    print("  watching clipboard — Ctrl-C to stop")
    count = 0
    try:
        while iterations is None or count < iterations:
            poll_once(source(), seen, cfg, client)
            count += 1
            if interval:
                time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  stopped")
    return 0
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/watch.py downloader/tests/test_watch.py
git commit -m "dl: add clipboard watcher"
```

---

## Task 11: Themes

**Files:**
- Create: `downloader/dl/theme.py`
- Test: `downloader/tests/test_theme.py`

**Interfaces:**
- Consumes: `dl.config.Config`, `dl.config.Category`
- Produces:
  - `Theme(name, accent, danger, ok, warn, dim, ramp: tuple[str, ...], mono: bool, icons: bool)`
  - `THEMES: dict[str, Theme]` — keys `aurora`, `ember`, `matrix`, `mono`
  - `select(cfg: Config, env: dict[str, str] | None = None) -> Theme`
  - `icon_for(category: Category, theme: Theme) -> str` — always exactly 2 display cells
  - `ramp_color(theme: Theme, position: float) -> str` — `position` in `[0, 1]`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_theme.py`:

```python
import pytest

from dl import config, theme


@pytest.fixture
def cfg():
    return config.defaults()


def test_all_four_themes_exist():
    assert set(theme.THEMES) == {"aurora", "ember", "matrix", "mono"}


def test_every_theme_has_a_non_empty_ramp():
    for t in theme.THEMES.values():
        assert len(t.ramp) >= 2
        assert all(c.startswith("#") for c in t.ramp)


def test_select_uses_config_theme(cfg):
    assert theme.select(cfg, env={}).name == "aurora"


def test_select_falls_back_for_unknown_name(cfg):
    broken = config.Config(
        config.replace(cfg.general, theme="neon"), cfg.limits, cfg.categories, cfg.domains
    )
    assert theme.select(broken, env={}).name == "aurora"


def test_no_color_forces_mono(cfg):
    assert theme.select(cfg, env={"NO_COLOR": "1"}).name == "mono"


def test_dumb_term_forces_mono(cfg):
    assert theme.select(cfg, env={"TERM": "dumb"}).name == "mono"


def test_mono_theme_is_marked_mono():
    assert theme.THEMES["mono"].mono is True
    assert theme.THEMES["aurora"].mono is False


def test_icon_for_emoji_theme_uses_category_icon(cfg):
    t = theme.THEMES["aurora"]
    assert theme.icon_for(cfg.categories["iso"], t) == "💿"


def test_icon_for_mono_theme_uses_ascii_tag(cfg):
    t = theme.THEMES["mono"]
    tag = theme.icon_for(cfg.categories["iso"], t)
    assert tag == "IS"
    assert len(tag) == 2


def test_icon_for_ascii_icons_config(cfg):
    disabled = config.replace(theme.THEMES["aurora"], icons=False)
    assert theme.icon_for(cfg.categories["video"], disabled) == "VI"


def test_ramp_color_endpoints(cfg):
    t = theme.THEMES["aurora"]
    assert theme.ramp_color(t, 0.0) == t.ramp[0]
    assert theme.ramp_color(t, 1.0) == t.ramp[-1]


def test_ramp_color_clamps(cfg):
    t = theme.THEMES["aurora"]
    assert theme.ramp_color(t, -5) == t.ramp[0]
    assert theme.ramp_color(t, 5) == t.ramp[-1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.theme'`

- [ ] **Step 3: Implement**

Create `downloader/dl/theme.py`:

```python
import os
from dataclasses import dataclass, replace

from .config import Category, Config


@dataclass(frozen=True)
class Theme:
    name: str
    accent: str
    danger: str
    ok: str
    warn: str
    dim: str
    ramp: tuple[str, ...]
    mono: bool
    icons: bool


THEMES: dict[str, Theme] = {
    "aurora": Theme(
        name="aurora",
        accent="#4ecdc4",
        danger="#ff5f56",
        ok="#5ac26a",
        warn="#e5a44b",
        dim="#6b7280",
        ramp=("#1f6feb", "#4ecdc4", "#5ac26a", "#e5c44b"),
        mono=False,
        icons=True,
    ),
    "ember": Theme(
        name="ember",
        accent="#e58a3c",
        danger="#ff5f56",
        ok="#e5c44b",
        warn="#e5a44b",
        dim="#7a6a5f",
        ramp=("#7a2c1d", "#e58a3c", "#e5c44b", "#fff0b3"),
        mono=False,
        icons=True,
    ),
    "matrix": Theme(
        name="matrix",
        accent="#3ddc84",
        danger="#ff5f56",
        ok="#3ddc84",
        warn="#a8e05f",
        dim="#2f5d3a",
        ramp=("#0d3b1e", "#1f7a3d", "#3ddc84", "#c8ffd8"),
        mono=False,
        icons=True,
    ),
    "mono": Theme(
        name="mono",
        accent="#ffffff",
        danger="#ffffff",
        ok="#ffffff",
        warn="#ffffff",
        dim="#999999",
        ramp=("#ffffff", "#ffffff"),
        mono=True,
        icons=False,
    ),
}

DEFAULT = "aurora"


def select(cfg: Config, env: dict[str, str] | None = None) -> Theme:
    environ = os.environ if env is None else env
    if environ.get("NO_COLOR") or environ.get("TERM") == "dumb":
        return THEMES["mono"]
    chosen = THEMES.get(cfg.general.theme, THEMES[DEFAULT])
    if cfg.general.ascii_icons:
        chosen = replace(chosen, icons=False)
    return chosen


def icon_for(category: Category, theme: Theme) -> str:
    if theme.icons:
        return category.icon
    return category.name[:2].upper().ljust(2)


def ramp_color(theme: Theme, position: float) -> str:
    if not theme.ramp:
        return theme.accent
    clamped = min(max(position, 0.0), 1.0)
    index = round(clamped * (len(theme.ramp) - 1))
    return theme.ramp[index]
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/theme.py downloader/tests/test_theme.py
git commit -m "dl: add theme palettes and glyph sets"
```

---

## Task 12: Row model and download table widget

**Files:**
- Create: `downloader/dl/tui/__init__.py`
- Create: `downloader/dl/tui/table.py`
- Test: `downloader/tests/test_table.py`

**Interfaces:**
- Consumes: `dl.format`, `dl.theme`, `dl.config`, `dl.routing`
- Produces:
  - `Row(gid, name, status, total, done, speed, eta, category, path, conns, error, history)` — `history` is a `list[int]`
  - `row_from_status(item: dict, cfg: Config) -> Row` — the string→int boundary conversion lives here
  - `columns_for_width(width: int) -> set[str]` — subset of `{"folder", "eta", "spark"}`
  - `bar_width_for(width: int) -> int`
  - `render_row(row: Row, theme: Theme, width: int, selected: bool, frame: int) -> list[str]` — returns the 2 or 3 markup lines for a card
  - `DownloadTable(Static)` — Textual widget with `.set_rows(rows: list[Row])`, `.selected_gid: str | None`, `.move(delta: int)`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_table.py`:

```python
import pytest

from dl import config, theme
from dl.tui.table import Row, bar_width_for, columns_for_width, render_row, row_from_status


@pytest.fixture
def cfg():
    return config.defaults()


@pytest.fixture
def th():
    return theme.THEMES["aurora"]


def status(**over):
    base = {
        "gid": "g1",
        "status": "active",
        "totalLength": "6127219712",
        "completedLength": "4294967296",
        "downloadSpeed": "8493465",
        "connections": "16",
        "files": [{"path": "/tmp/ubuntu.iso", "uris": [{"uri": "https://e.com/ubuntu.iso"}]}],
        "errorMessage": "",
    }
    base.update(over)
    return base


def test_row_from_status_converts_every_numeric_string(cfg):
    row = row_from_status(status(), cfg)
    assert row.total == 6127219712
    assert row.done == 4294967296
    assert row.speed == 8493465
    assert row.conns == 16
    assert all(isinstance(v, int) for v in (row.total, row.done, row.speed, row.conns))


def test_row_from_status_picks_name_and_category(cfg):
    row = row_from_status(status(), cfg)
    assert row.name == "ubuntu.iso"
    assert row.category.name == "iso"


def test_row_from_status_eta_from_speed(cfg):
    row = row_from_status(status(), cfg)
    assert row.eta == (6127219712 - 4294967296) // 8493465


def test_row_from_status_eta_is_negative_when_stalled(cfg):
    row = row_from_status(status(downloadSpeed="0"), cfg)
    assert row.eta < 0


def test_row_from_status_handles_unknown_total(cfg):
    row = row_from_status(status(totalLength="0"), cfg)
    assert row.total == 0
    assert row.eta < 0


def test_row_from_status_missing_files_is_safe(cfg):
    row = row_from_status(status(files=[]), cfg)
    assert row.name == ""
    assert row.category.name == "other"


def test_row_from_status_carries_error_message(cfg):
    row = row_from_status(status(status="error", errorMessage="HTTP 403"), cfg)
    assert row.error == "HTTP 403"


def test_columns_drop_in_order_as_width_shrinks():
    assert columns_for_width(100) == {"folder", "eta", "spark"}
    assert "folder" not in columns_for_width(78)
    assert "eta" not in columns_for_width(64)
    assert columns_for_width(52) == set()


def test_bar_width_shrinks_but_never_below_four():
    assert bar_width_for(100) >= bar_width_for(60)
    assert bar_width_for(50) >= 4
    assert bar_width_for(20) >= 4


def test_render_row_produces_two_lines_when_unselected(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    assert len(lines) == 2


def test_render_row_selected_but_collapsed_is_still_two_lines(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=True, frame=0)
    assert len(lines) == 2


def test_render_row_selected_and_expanded_adds_detail_line(cfg, th):
    lines = render_row(
        row_from_status(status(), cfg), th, 100, selected=True, frame=0, expanded=True
    )
    assert len(lines) == 3
    assert "/tmp/ubuntu.iso" in lines[2]
    assert "16 conns" in lines[2]


def test_render_row_expanded_but_unselected_adds_nothing(cfg, th):
    lines = render_row(
        row_from_status(status(), cfg), th, 100, selected=False, frame=0, expanded=True
    )
    assert len(lines) == 2


def test_render_row_selected_gets_accent_marker(cfg, th):
    selected = render_row(row_from_status(status(), cfg), th, 100, selected=True, frame=0)
    plain = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    assert "▌" in selected[0]
    assert "▌" not in plain[0]


def test_render_row_shows_name_and_sizes(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    joined = " ".join(lines)
    assert "ubuntu.iso" in joined
    assert "5.7 GB" in joined
    assert "70%" in joined


def test_render_row_paused_shows_paused_not_speed(cfg, th):
    row = row_from_status(status(status="paused", downloadSpeed="0"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=0))
    assert "paused" in joined


def test_render_row_error_shows_message_and_retry_hint(cfg, th):
    row = row_from_status(status(status="error", errorMessage="HTTP 403"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=0))
    assert "HTTP 403" in joined
    assert "retry" in joined


def test_render_row_mono_theme_has_no_color_markup(cfg):
    row = row_from_status(status(), cfg)
    lines = render_row(row, theme.THEMES["mono"], 100, selected=False, frame=0)
    assert "[#" not in " ".join(lines)


def test_render_row_narrow_hides_folder(cfg, th):
    row = row_from_status(status(), cfg)
    joined = " ".join(render_row(row, th, 60, selected=False, frame=0))
    assert "ISO" not in joined


def test_render_row_queued_uses_spinner_frame(cfg, th):
    from dl.format import SPINNER

    row = row_from_status(status(status="waiting", downloadSpeed="0"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=3))
    assert SPINNER[3] in joined
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui'`

- [ ] **Step 3: Implement**

Create `downloader/dl/tui/__init__.py` (empty file):

```python
```

Create `downloader/dl/tui/table.py`:

```python
from dataclasses import dataclass, field
from pathlib import Path

from textual.widgets import Static

from ..config import Category, Config
from ..format import SPINNER, human_bytes, human_duration, human_speed, progress_bar, sparkline
from ..routing import OTHER, resolve
from ..theme import Theme, icon_for, ramp_color

GLYPH = {"active": "▸", "paused": "⏸", "waiting": "⋯", "complete": "✅", "error": "❌"}


def escape(text: str) -> str:
    return text.replace("[", "\\[")


@dataclass
class Row:
    gid: str
    name: str
    status: str
    total: int
    done: int
    speed: int
    eta: int
    category: Category
    path: Path
    conns: int
    error: str
    history: list[int] = field(default_factory=list)

    @property
    def pct(self) -> float:
        return (self.done * 100.0 / self.total) if self.total else 0.0


def row_from_status(item: dict, cfg: Config) -> Row:
    files = item.get("files") or [{}]
    first = files[0]
    path = Path(first.get("path", "") or "")
    uris = first.get("uris") or []
    url = uris[0].get("uri", "") if uris else ""
    total = int(item.get("totalLength", 0) or 0)
    done = int(item.get("completedLength", 0) or 0)
    speed = int(item.get("downloadSpeed", 0) or 0)
    category = resolve(url, path.name, cfg).category if path.name or url else OTHER
    eta = (total - done) // speed if speed > 0 and total > done else -1
    return Row(
        gid=item.get("gid", ""),
        name=path.name,
        status=item.get("status", ""),
        total=total,
        done=done,
        speed=speed,
        eta=eta,
        category=category,
        path=path,
        conns=int(item.get("connections", 0) or 0),
        error=item.get("errorMessage", "") or "",
    )


def columns_for_width(width: int) -> set[str]:
    columns = set()
    if width >= 80:
        columns.add("folder")
    if width >= 66:
        columns.add("eta")
    if width >= 56:
        columns.add("spark")
    return columns


def bar_width_for(width: int) -> int:
    return max(4, min(26, (width - 46) // 2 + 8))


def _paint(text: str, color: str, theme: Theme) -> str:
    return text if theme.mono else f"[{color}]{text}[/]"


def _gradient_bar(row: Row, theme: Theme, width: int) -> str:
    plain = progress_bar(row.pct, width)
    if theme.mono:
        return plain
    out = []
    for i, ch in enumerate(plain):
        out.append(f"[{ramp_color(theme, i / max(width - 1, 1))}]{ch}[/]")
    return "".join(out)


def _state_cell(row: Row, theme: Theme, frame: int) -> str:
    if row.status == "error":
        return _paint(f"❌ {escape(row.error or 'failed')} — press r to retry", theme.danger, theme)
    if row.status == "paused":
        return _paint("⏸  paused", theme.warn, theme)
    if row.status in ("waiting", "queued"):
        return _paint(f"{SPINNER[frame % len(SPINNER)]}  queued", theme.dim, theme)
    if row.status == "complete":
        return _paint("✅ done", theme.ok, theme)
    return _paint(f"🚀 {human_speed(row.speed)}", theme.accent, theme)


def render_row(
    row: Row, theme: Theme, width: int, selected: bool, frame: int, expanded: bool = False
) -> list[str]:
    columns = columns_for_width(width)
    marker = _paint("▌", row.category.hue, theme) if selected else " "
    icon = icon_for(row.category, theme)
    sizes = f"{human_bytes(row.done)} / {human_bytes(row.total)}" if row.total else human_bytes(row.done)

    head = f"{marker} {icon}  {escape(row.name) or '(resolving…)':<44} {sizes:>20}"

    parts = [
        f"{marker}     {_gradient_bar(row, theme, bar_width_for(width))}",
        f"{row.pct:>4.0f}%",
        _state_cell(row, theme, frame),
    ]
    if "spark" in columns:
        parts.append(_paint(sparkline(row.history, 8), theme.dim, theme))
    if "eta" in columns:
        parts.append(_paint(f"⏱ {human_duration(row.eta)}", theme.dim, theme))
    if "folder" in columns:
        parts.append(_paint(row.category.name.upper(), row.category.hue, theme))
    body = "  ".join(parts)

    lines = [head, body]
    if selected and expanded:
        lines.append(f"{marker}     📂 {escape(str(row.path))} · {row.conns} conns")
    return lines


class DownloadTable(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.rows: list[Row] = []
        self.cursor = 0
        self.frame = 0
        self.expanded = False

    @property
    def selected_gid(self) -> str | None:
        if not self.rows:
            return None
        return self.rows[min(self.cursor, len(self.rows) - 1)].gid

    def move(self, delta: int) -> None:
        if not self.rows:
            return
        self.cursor = max(0, min(len(self.rows) - 1, self.cursor + delta))
        self.refresh_view()

    def set_rows(self, rows: list[Row]) -> None:
        previous = {r.gid: r.history for r in self.rows}
        for row in rows:
            row.history = (previous.get(row.gid, []) + [row.speed])[-8:]
        self.rows = rows
        self.cursor = min(self.cursor, max(len(rows) - 1, 0))
        self.refresh_view()

    def refresh_view(self) -> None:
        self.frame += 1
        width = self.size.width or 100
        lines: list[str] = []
        for index, row in enumerate(self.rows):
            selected = index == self.cursor
            lines.extend(
                render_row(row, self.theme_data, width, selected, self.frame, selected and self.expanded)
            )
            lines.append("")
        self.update("\n".join(lines))

    def render_lines_count(self) -> list[str]:
        return str(self.renderable).splitlines()
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/tui/__init__.py downloader/dl/tui/table.py downloader/tests/test_table.py
git commit -m "dl: add download row model and table widget"
```

---

## Task 13: Status bar widget

**Files:**
- Create: `downloader/dl/tui/status.py`
- Test: `downloader/tests/test_status.py`

**Interfaces:**
- Consumes: `dl.format`, `dl.theme`
- Produces:
  - `Stats(speed: int, active: int, waiting: int, done: int, limit: str, elapsed: int)`
  - `stats_from(global_stat: dict, limit: str, elapsed: int) -> Stats`
  - `render_status(stats: Stats, history: list[int], theme: Theme, width: int) -> str`
  - `StatusBar(Static)` with `.update_stats(stats: Stats)` and an internal 40-sample ring

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_status.py`:

```python
import pytest

from dl import theme
from dl.tui.status import render_status, stats_from


@pytest.fixture
def th():
    return theme.THEMES["aurora"]


def gstat(**over):
    base = {"downloadSpeed": "13002342", "numActive": "3", "numWaiting": "2", "numStopped": "47"}
    base.update(over)
    return base


def test_stats_from_converts_strings_to_ints():
    s = stats_from(gstat(), "0", 261)
    assert (s.speed, s.active, s.waiting, s.done) == (13002342, 3, 2, 47)
    assert all(isinstance(v, int) for v in (s.speed, s.active, s.waiting, s.done))


def test_stats_from_handles_missing_keys():
    s = stats_from({}, "0", 0)
    assert (s.speed, s.active, s.waiting, s.done) == (0, 0, 0, 0)


def test_render_status_shows_speed_and_counts(th):
    out = render_status(stats_from(gstat(), "0", 261), [1, 2, 3], th, 100)
    assert "12.4 MB/s" in out
    assert "3" in out and "2" in out and "47" in out


def test_render_status_shows_limit_off_when_zero(th):
    out = render_status(stats_from(gstat(), "0", 0), [], th, 100)
    assert "off" in out


def test_render_status_shows_limit_value_when_set(th):
    out = render_status(stats_from(gstat(), "2M", 0), [], th, 100)
    assert "2M" in out


def test_render_status_includes_elapsed(th):
    out = render_status(stats_from(gstat(), "0", 261), [], th, 100)
    assert "4m 21s" in out


def test_render_status_mono_has_no_color_markup():
    out = render_status(stats_from(gstat(), "0", 0), [1, 2], theme.THEMES["mono"], 100)
    assert "[#" not in out


def test_render_status_narrow_still_fits(th):
    out = render_status(stats_from(gstat(), "0", 0), [1, 2, 3], th, 50)
    assert out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui.status'`

- [ ] **Step 3: Implement**

Create `downloader/dl/tui/status.py`:

```python
from dataclasses import dataclass

from textual.widgets import Static

from ..format import human_duration, human_speed, sparkline
from ..theme import Theme, ramp_color


@dataclass(frozen=True)
class Stats:
    speed: int
    active: int
    waiting: int
    done: int
    limit: str
    elapsed: int


def stats_from(global_stat: dict, limit: str, elapsed: int) -> Stats:
    return Stats(
        speed=int(global_stat.get("downloadSpeed", 0) or 0),
        active=int(global_stat.get("numActive", 0) or 0),
        waiting=int(global_stat.get("numWaiting", 0) or 0),
        done=int(global_stat.get("numStopped", 0) or 0),
        limit=limit,
        elapsed=elapsed,
    )


def _graph(history: list[int], theme: Theme, width: int) -> str:
    line = sparkline(history, width)
    if theme.mono:
        return line
    peak = max(history) if history else 0
    out = []
    window = ([0] * width + list(history))[-width:]
    for value, glyph in zip(window, line):
        position = (value / peak) if peak else 0.0
        out.append(f"[{ramp_color(theme, position)}]{glyph}[/]")
    return "".join(out)


def render_status(stats: Stats, history: list[int], theme: Theme, width: int) -> str:
    graph_width = 40 if width >= 90 else (20 if width >= 66 else 10)
    limit = "off" if stats.limit in ("0", "", "off") else stats.limit
    speed = human_speed(stats.speed)
    counts = f"↓{stats.active}  ⏳{stats.waiting}  ✅{stats.done}"
    tail = f"🚦 {limit}   ⏱ {human_duration(stats.elapsed)}"
    if theme.mono:
        return f"{speed}   {sparkline(history, graph_width)}   {counts}   {tail}"
    return (
        f"[{theme.accent}]🚀 {speed}[/]   {_graph(history, theme, graph_width)}   "
        f"[{theme.dim}]{counts}[/]   [{theme.dim}]{tail}[/]"
    )


class StatusBar(Static):
    def __init__(self, theme: Theme, **kwargs):
        super().__init__("", markup=True, **kwargs)
        self.theme_data = theme
        self.history: list[int] = []

    def update_stats(self, stats: Stats) -> None:
        self.history = (self.history + [stats.speed])[-40:]
        self.update(render_status(stats, self.history, self.theme_data, self.size.width or 100))
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/tui/status.py downloader/tests/test_status.py
git commit -m "dl: add status bar with live throughput graph"
```

---

## Task 14: Modals and the Textual app

**Files:**
- Create: `downloader/dl/tui/modals.py`
- Create: `downloader/dl/tui/app.py`
- Create: `downloader/tests/conftest.py`
- Test: `downloader/tests/test_app.py`

**Interfaces:**
- Consumes: everything above
- Produces:
  - `AddUrlModal(ModalScreen)` — dismisses with `list[str]` or `None`
  - `SpeedLimitModal(ModalScreen)` — dismisses with `str` or `None`
  - `ConfirmModal(ModalScreen)` — dismisses with `bool`
  - `DlApp(App)` with bindings and `action_*` methods
  - `run_tui(cfg: Config, client) -> int`
  - `SPLASH: str`

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_app.py`:

```python
import pytest

from dl import config
from dl.tui.app import DlApp


@pytest.fixture
def cfg():
    return config.defaults()


class FakeClient:
    def __init__(self):
        self.paused = []
        self.unpaused = []
        self.removed = []
        self.positions = []
        self.global_options = {}
        self.added = []
        self.active = [
            {
                "gid": "g1",
                "status": "active",
                "totalLength": "1000",
                "completedLength": "500",
                "downloadSpeed": "100",
                "connections": "8",
                "files": [{"path": "/tmp/a.iso", "uris": [{"uri": "https://e.com/a.iso"}]}],
            },
            {
                "gid": "g2",
                "status": "active",
                "totalLength": "2000",
                "completedLength": "100",
                "downloadSpeed": "50",
                "connections": "4",
                "files": [{"path": "/tmp/b.mkv", "uris": [{"uri": "https://e.com/b.mkv"}]}],
            },
        ]

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return []

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_global_stat(self):
        return {"downloadSpeed": "150", "numActive": "2", "numWaiting": "0", "numStopped": "5"}

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        return "g9"

    def pause(self, gid):
        self.paused.append(gid)

    def unpause(self, gid):
        self.unpaused.append(gid)

    def remove(self, gid):
        self.removed.append(gid)

    def change_position(self, gid, pos, how):
        self.positions.append((gid, pos, how))
        return 0

    def change_global_option(self, options):
        self.global_options.update(options)
        return "OK"


async def test_app_starts_and_lists_rows(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(app.table.rows) == 2
        assert app.table.rows[0].name == "a.iso"


async def test_space_pauses_selected(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.paused == ["g1"]


async def test_space_on_paused_row_resumes(cfg):
    client = FakeClient()
    client.active[0]["status"] = "paused"
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.unpaused == ["g1"]


async def test_down_then_space_targets_second_row(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        await pilot.press("space")
        assert client.paused == ["g2"]


async def test_shift_j_moves_row_down_in_queue(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("J")
        assert client.positions == [("g1", 1, "POS_CUR")]


async def test_shift_k_moves_row_up_in_queue(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("K")
        assert client.positions == [("g1", -1, "POS_CUR")]


async def test_p_pauses_all_and_u_resumes_all(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert client.paused == ["g1", "g2"]
        await pilot.press("u")
        assert client.unpaused == ["g1", "g2"]


async def test_tab_switches_to_completed_view(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.showing_completed is False
        await pilot.press("tab")
        assert app.showing_completed is True


async def test_enter_toggles_the_detail_line(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.table.expanded is False
        before = len(app.table.render_lines_count())
        await pilot.press("enter")
        assert app.table.expanded is True
        assert len(app.table.render_lines_count()) == before + 1
        await pilot.press("enter")
        assert len(app.table.render_lines_count()) == before


async def test_q_quits(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.is_running is False


async def test_lost_daemon_sets_disconnected_flag(cfg):
    from dl.rpc import Aria2Unreachable

    class DeadClient(FakeClient):
        def tell_active(self):
            raise Aria2Unreachable("gone")

        def get_global_stat(self):
            raise Aria2Unreachable("gone")

    app = DlApp(cfg, DeadClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.disconnected is True
```

Bare `pytest` does not run `async def` tests, and `pytest-asyncio` is forbidden by the single-dev-dependency constraint. Create `downloader/tests/conftest.py` to run coroutine tests directly — this is the only supported approach for this project:

```python
import asyncio
import inspect


def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd downloader && make test`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui.app'`

- [ ] **Step 3: Implement the modals**

Create `downloader/dl/tui/modals.py`:

```python
import subprocess

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea


def clipboard_text() -> str:
    try:
        value = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, check=False, timeout=2
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""
    return value if value.startswith(("http://", "https://", "ftp://", "magnet:")) else ""


class AddUrlModal(ModalScreen[list[str] | None]):
    BINDINGS = [("escape", "dismiss_none", "cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Label("Add downloads — one URL per line")
            yield TextArea(clipboard_text(), id="urls")
            yield Label("", id="preview")
            yield Button("Queue", variant="primary", id="ok")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self._submit()

    def action_dismiss_none(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        raw = self.query_one("#urls", TextArea).text
        urls = [line.strip() for line in raw.splitlines() if line.strip()]
        self.dismiss(urls or None)


class SpeedLimitModal(ModalScreen[str | None]):
    BINDINGS = [("escape", "dismiss_none", "cancel")]

    def __init__(self, current: str):
        super().__init__()
        self.current = "off" if current in ("0", "", "off") else current

    def compose(self) -> ComposeResult:
        with Vertical(id="limit-box"):
            yield Label("Speed limit — e.g. 2M, 500K, or off")
            yield Input(self.current, id="rate")
            yield Button("Apply", variant="primary", id="ok")

    def on_button_pressed(self, _event: Button.Pressed) -> None:
        self.dismiss(self.query_one("#rate", Input).value.strip())

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.dismiss(self.query_one("#rate", Input).value.strip())

    def action_dismiss_none(self) -> None:
        self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [("escape", "dismiss_false", "cancel")]

    def __init__(self, question: str):
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Label(self.question)
            yield Button("Yes", variant="error", id="yes")
            yield Button("No", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_dismiss_false(self) -> None:
        self.dismiss(False)
```

- [ ] **Step 4: Implement the app**

Create `downloader/dl/tui/app.py`:

```python
import time
from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

from .. import cli, config, history, routing, theme
from ..config import STATE_DIR, Config
from ..format import human_bytes, human_duration
from ..rpc import Aria2Error, Aria2Unreachable
from .modals import AddUrlModal, ConfirmModal, SpeedLimitModal
from .status import StatusBar, stats_from
from .table import DownloadTable, row_from_status

SPLASH = """\
                    ██████╗ ██╗
                    ██╔══██╗██║        d o w n l o a d e r
                    ██║  ██║██║        ─────────────────────
                    ██████╔╝███████╗   ⚡ powered by aria2
                    ╚═════╝ ╚══════╝
                         ▼ ▼ ▼
"""

CSS = """
Screen { layout: vertical; }
StatusBar { height: 1; dock: top; padding: 0 1; }
#body { height: 1fr; padding: 0 1; }
#hint { dock: bottom; height: 1; padding: 0 1; }
AddUrlModal, SpeedLimitModal, ConfirmModal { align: center middle; }
#add-box, #limit-box, #confirm-box {
    width: 70; padding: 1 2; border: round $accent; background: $surface;
}
#urls { height: 8; }
"""

HINT = (
    "a add   space pause/resume   d delete   J K reorder   l limit   "
    "o open   / filter   tab completed   q quit"
)


class DlApp(App):
    CSS = CSS
    BINDINGS = [
        ("a", "add", "add"),
        ("space", "toggle", "pause/resume"),
        ("d", "delete", "delete"),
        ("J", "move_down", "down"),
        ("K", "move_up", "up"),
        ("l", "limit", "limit"),
        ("L", "limit_one", "limit one"),
        ("o", "reveal", "reveal"),
        ("p", "pause_all", "pause all"),
        ("u", "resume_all", "resume all"),
        ("r", "retry", "retry"),
        ("tab", "toggle_tab", "completed"),
        ("enter", "expand", "expand"),
        ("down", "cursor_down", "down"),
        ("up", "cursor_up", "up"),
        ("q", "quit", "quit"),
    ]

    def __init__(self, cfg: Config, client):
        super().__init__()
        self.cfg = cfg
        self.client = client
        self.theme_data = theme.select(cfg)
        self.started = time.monotonic()
        self.showing_completed = False
        self.disconnected = False
        self.limit = cfg.limits.global_rate
        self.status = StatusBar(self.theme_data)
        self.table = DownloadTable(self.theme_data, id="table")
        self.completed = Static("", markup=True, id="completed")

    def compose(self) -> ComposeResult:
        yield self.status
        with VerticalScroll(id="body"):
            yield self.table
            yield self.completed
        yield Static(HINT, id="hint")

    def on_mount(self) -> None:
        self.completed.display = False
        self.set_interval(0.5, self.refresh_data)
        self.set_interval(0.1, self.table.refresh_view)
        self.call_after_refresh(self.refresh_data)

    async def refresh_data(self) -> None:
        try:
            items = list(self.client.tell_active()) + list(self.client.tell_waiting())
            stat = self.client.get_global_stat()
        except (Aria2Unreachable, Aria2Error):
            self.disconnected = True
            self.status.update(f"[{self.theme_data.danger}]⚠ daemon lost — reconnecting[/]")
            return
        self.disconnected = False
        self.table.set_rows([row_from_status(item, self.cfg) for item in items])
        elapsed = int(time.monotonic() - self.started)
        self.status.update_stats(stats_from(stat, self.limit, elapsed))
        if not items and not self.showing_completed:
            self.table.update(f"[{self.theme_data.accent}]{SPLASH}[/]\n   press a to add a download")

    def _selected(self):
        gid = self.table.selected_gid
        if gid is None:
            return None
        return next((r for r in self.table.rows if r.gid == gid), None)

    def action_cursor_down(self) -> None:
        self.table.move(1)

    def action_cursor_up(self) -> None:
        self.table.move(-1)

    def action_expand(self) -> None:
        self.table.expanded = not self.table.expanded
        self.table.refresh_view()

    def action_toggle(self) -> None:
        row = self._selected()
        if row is None:
            return
        if row.status == "paused":
            self.client.unpause(row.gid)
        else:
            self.client.pause(row.gid)

    def action_pause_all(self) -> None:
        for row in self.table.rows:
            self.client.pause(row.gid)

    def action_resume_all(self) -> None:
        for row in self.table.rows:
            self.client.unpause(row.gid)

    def action_move_down(self) -> None:
        row = self._selected()
        if row:
            self.client.change_position(row.gid, 1, "POS_CUR")

    def action_move_up(self) -> None:
        row = self._selected()
        if row:
            self.client.change_position(row.gid, -1, "POS_CUR")

    def action_retry(self) -> None:
        row = self._selected()
        if row is None or row.status != "error":
            return
        resolution = routing.resolve("", row.name, self.cfg)
        self.client.add_uri([str(row.path)], cli.add_options(self.cfg, resolution))

    def action_reveal(self) -> None:
        import subprocess

        row = self._selected()
        if row and row.path:
            subprocess.run(["open", "-R", str(row.path)], check=False)

    def action_toggle_tab(self) -> None:
        self.showing_completed = not self.showing_completed
        self.table.display = not self.showing_completed
        self.completed.display = self.showing_completed
        if self.showing_completed:
            self._render_completed()

    def _render_completed(self) -> None:
        rows = history.tail(STATE_DIR / "history.jsonl", 50)[::-1]
        lines = []
        for record in rows:
            mark = "✅" if record.get("status") == "ok" else "❌"
            lines.append(
                f"  {mark}  {record.get('name', ''):<38} "
                f"{human_bytes(int(record.get('bytes', 0) or 0)):>10}  "
                f"{record.get('category', ''):<9} "
                f"{human_duration(int(time.time()) - int(record.get('ts', 0) or 0))} ago"
            )
        self.completed.update("\n".join(lines) or "  (nothing finished yet)")

    def action_add(self) -> None:
        def queue(urls: list[str] | None) -> None:
            if not urls:
                return
            for url in urls:
                name = routing.filename_from_url(url)
                resolution = routing.resolve(url, name, self.cfg)
                resolution.path.mkdir(parents=True, exist_ok=True)
                self.client.add_uri([url], cli.add_options(self.cfg, resolution))

        self.push_screen(AddUrlModal(), queue)

    def action_limit(self) -> None:
        def apply(rate: str | None) -> None:
            if rate is None:
                return
            value = config.parse_rate(rate)
            self.client.change_global_option({"max-overall-download-limit": value})
            self.limit = value

        self.push_screen(SpeedLimitModal(self.limit), apply)

    def action_limit_one(self) -> None:
        row = self._selected()
        if row is None:
            return

        def apply(rate: str | None) -> None:
            if rate is not None:
                self.client.change_option(row.gid, {"max-download-limit": config.parse_rate(rate)})

        self.push_screen(SpeedLimitModal("off"), apply)

    def action_delete(self) -> None:
        row = self._selected()
        if row is None:
            return

        def confirm(yes: bool) -> None:
            if yes:
                self.client.remove(row.gid)

        if row.done < row.total:
            self.push_screen(ConfirmModal(f"Delete {row.name}? It is incomplete."), confirm)
        else:
            self.client.remove(row.gid)


def run_tui(cfg: Config, client) -> int:
    DlApp(cfg, client).run()
    return 0
```

- [ ] **Step 5: Run to verify it passes**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 6: Manual smoke check**

```bash
cd downloader && make install
dl https://speed.hetzner.de/100MB.bin
dl
```

Expected: the URL queues with a category line; the TUI opens, shows a live bar and sparkline, `space` pauses, `q` exits, and `dl ls` still shows the download running afterwards.

- [ ] **Step 7: Commit**

```bash
git add downloader/dl/tui/modals.py downloader/dl/tui/app.py downloader/tests/conftest.py downloader/tests/test_app.py
git commit -m "dl: add textual app, modals, keymap and refresh loop"
```

---

## Task 15: End-to-end integration test

**Files:**
- Create: `downloader/tests/test_integration.py`

**Interfaces:**
- Consumes: everything
- Produces: nothing importable — this is the gate proving the pieces work together

Skipped automatically when `aria2c` is not installed, so the suite stays green on machines without it.

- [ ] **Step 1: Write the test**

Create `downloader/tests/test_integration.py`:

```python
import functools
import json
import shutil
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from dl import config, daemon, history, routing
from dl.cli import add_options

pytestmark = pytest.mark.skipif(shutil.which("aria2c") is None, reason="aria2c not installed")

PAYLOAD = b"x" * (5 * 1024 * 1024)


@pytest.fixture
def fileserver(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "sample.iso").write_bytes(PAYLOAD)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    downloads = tmp_path / "downloads"
    cats = dict(config.DEFAULT_CATEGORIES)
    cats["iso"] = config.Category("iso", downloads / "ISO", ("iso",), "💿", "#4aa3ff")
    general = config.replace(
        config.defaults().general, default_dir=downloads / "other", idle_timeout=2
    )
    cfg = config.Config(general, config.defaults().limits, cats, {})
    monkeypatch.setenv("DL_STATE_DIR", str(state))
    monkeypatch.setattr(config, "STATE_DIR", state)
    monkeypatch.setattr(daemon, "STATE_DIR", state)
    yield cfg, state
    try:
        daemon.ensure_running(cfg, state).shutdown()
    except Exception:
        pass


def wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_daemon_starts_and_answers(env):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    assert "version" in client.get_version()
    assert (state / "rpc.secret").exists()
    assert (state / "hooks" / "complete.sh").exists()


def test_second_ensure_running_reuses_the_same_daemon(env):
    cfg, state = env
    first = daemon.ensure_running(cfg, state)
    second = daemon.ensure_running(cfg, state)
    assert first.port == second.port


def test_full_download_lands_in_the_routed_directory(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    url = f"{fileserver}/sample.iso"
    resolution = routing.resolve(url, "sample.iso", cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    gid = client.add_uri([url], add_options(cfg, resolution))

    target = cfg.categories["iso"].dir / "sample.iso"
    assert wait_for(lambda: target.exists() and target.stat().st_size == len(PAYLOAD))
    assert client.tell_status(gid)["status"] == "complete"


def test_hook_writes_a_history_row(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    url = f"{fileserver}/sample.iso"
    resolution = routing.resolve(url, "sample.iso", cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    client.add_uri([url], add_options(cfg, resolution))

    log = state / "history.jsonl"
    assert wait_for(lambda: log.exists() and history.tail(log, 5))
    record = history.tail(log, 5)[-1]
    assert record["name"] == "sample.iso"
    assert record["status"] == "ok"
    assert record["bytes"] == len(PAYLOAD)


def test_pause_then_resume_mid_transfer(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    client.change_global_option({"max-overall-download-limit": "64K"})
    url = f"{fileserver}/sample.iso"
    resolution = routing.resolve(url, "sample.iso", cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    gid = client.add_uri([url], add_options(cfg, resolution))

    assert wait_for(lambda: client.tell_status(gid)["status"] == "active")
    client.pause(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] == "paused")
    client.unpause(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] in ("active", "waiting"))
    client.change_global_option({"max-overall-download-limit": "0"})


def test_remove_deletes_from_the_queue(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    client.change_global_option({"max-overall-download-limit": "64K"})
    url = f"{fileserver}/sample.iso"
    resolution = routing.resolve(url, "sample.iso", cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    gid = client.add_uri([url], add_options(cfg, resolution))
    assert wait_for(lambda: client.tell_status(gid)["status"] == "active")
    client.remove(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] in ("removed", "error"))
    client.change_global_option({"max-overall-download-limit": "0"})


def test_failed_download_records_an_error_row(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    url = f"{fileserver}/missing.iso"
    resolution = routing.resolve(url, "missing.iso", cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    client.add_uri([url], {**add_options(cfg, resolution), "max-tries": "1"})

    log = state / "history.jsonl"
    assert wait_for(
        lambda: log.exists() and any(r["status"] == "error" for r in history.tail(log, 10)),
        timeout=40,
    )


def test_no_test_touched_the_network(env):
    cfg, state = env
    args = daemon.aria2_args(cfg, state, 6810, "x")
    assert "--rpc-listen-all=false" in args
```

- [ ] **Step 2: Run it and verify it passes**

Run: `cd downloader && make test`
Expected: PASS — integration tests included (they skip only if `aria2c` is absent). Total runtime under ~60s.

- [ ] **Step 3: Verify the suite is hermetic**

Run: `cd downloader && make test` with WiFi off.
Expected: identical results. If anything fails, a test is reaching the internet — fix it before continuing.

- [ ] **Step 4: Commit**

```bash
git add downloader/tests/test_integration.py
git commit -m "dl: add hermetic end-to-end integration tests"
```

---

## Task 16: Documentation

**Files:**
- Create: `downloader/README.md`
- Modify: `README.md` (arsenal root)

**Interfaces:**
- Consumes: nothing
- Produces: nothing importable

- [ ] **Step 1: Write the tool README**

Create `downloader/README.md` documenting, in this order: what `dl` is and the aria2c architecture in two sentences; install (`make install`, `brew install aria2`); the full CLI surface copied from `USAGE` in `__main__.py`; the keymap table from the spec; the `config.toml` reference with every key and its default; the four theme names; the file locations table from the spec §1; and a troubleshooting section covering `aria2c not found`, a stuck daemon (`dl kill`), and a corrupt session (`session.bad`).

End it with the manual verification checklist the automated suite deliberately skips:

```markdown
## Manual checklist

- [ ] Splash renders correctly and clears after ~700ms
- [ ] Progress bars animate smoothly; the comet tail is visible
- [ ] Header sparkline changes colour with throughput
- [ ] Emoji render in your terminal font and columns stay aligned
- [ ] `ascii_icons = true` keeps alignment with no emoji
- [ ] `NO_COLOR=1 dl` emits no colour
- [ ] Resize below 80, 66, and 50 columns — layout degrades, never scrolls sideways
- [ ] macOS notification appears on completion
- [ ] Ctrl-C in the TUI leaves downloads running (`dl ls` confirms)
- [ ] All four themes look correct
```

- [ ] **Step 2: Add `dl` to the arsenal README**

In `README.md`, add a `### \`dl\` — Download Manager` section between the `net-reset` and `vpn` sections, matching the existing style: a one-line description, a fenced usage block, and a short note that it needs `make install` from `downloader/` rather than a symlink. Add `aria2` to the Dependencies table (`brew install aria2`).

- [ ] **Step 3: Verify the documented commands actually work**

Run each command in `downloader/README.md`'s CLI section against the installed `dl`, including `dl --help`, `dl ls`, and `dl limit off`.
Expected: no command errors or prints usage unexpectedly.

- [ ] **Step 4: Commit**

```bash
git add downloader/README.md README.md
git commit -m "docs: document dl download manager"
```

---

## Self-Review Notes

**Spec coverage.** Every spec section maps to a task: §1 architecture → Tasks 1, 7; §2 components → Tasks 2–14 one module each; §3 UX → Tasks 11–14; §4 config/routing/flows → Tasks 3, 4, 5, 8, 10; §5 errors → error tests distributed through Tasks 3, 7, 8, 9, 14; §5 security → Task 7 (`test_read_secret_creates_file_with_0600`, `test_aria2_args_include_rpc_secret_and_localhost`); §5 testing → Tasks 2–15 plus the manual checklist in Task 16.

**Deviations from the spec, and why.** Three modules were added: `format.py` (spec §5 requires formatter tests but §2 omitted the module), `watch.py` (spec put `dl watch` in `cli.py`; splitting keeps `cli.py` focused and lets the poller be tested without a TTY), and `theme.py` (spec §3 says themes are "data, not code" — this is that data). Torrent and magnet support needs no dedicated task: `add_uri` accepts magnet URIs unchanged, `is_downloadable` already admits the `magnet:` scheme, and aria2 handles the rest.

**Known risk to watch during execution.** Task 14's async test harness depends on how Textual exposes `pytest` integration in the installed version. If `async def` tests are not collected, use the `asyncio.run()` wrapper shown in Task 14 Step 1 rather than adding `pytest-asyncio` — the single-dev-dependency constraint is deliberate.
