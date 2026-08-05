# `dl` Settings Screen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Edit every `dl` setting from inside the app, with changes taking effect immediately and no daemon restart.

**Architecture:** A declarative schema (`dl/settings.py`) describes each scalar setting: where it lives in the TOML, its type, allowed values. One screen class renders any list of those fields, so General, Limits, YouTube and Hooks are the same code with different field tuples. Three list-shaped sections (proxy domains, per-host headers, categories) get bespoke screens. Writes go through tomlkit (`dl/tomlio.py`) so comments and layout survive. After a save the config is re-read from disk and pushed into the running app.

**Tech Stack:** Python 3.11+, Textual, tomlkit, pytest. aria2 via JSON-RPC.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-06-dl-settings-screen-design.md`
- Working directory for all commands: `/Users/farhad/arsenal/downloader`
- Run tests with `~/.local/share/dl/venv/bin/python -m pytest` (the project's private venv, installed editable)
- Comments: this repo writes almost none. Only write a comment for a non-obvious *why*. Never restate the code. Match the density of the file being edited.
- Validators must reuse `config.parse_duration` and `destinations.ensure_writable` rather than introducing a second dialect for the same values.
- `live=True` applies to exactly two fields: `general.theme` and `general.ascii_icons`.
- Only `max_concurrent` is pushed to the running daemon. `connections`, `splits`, `min_split` and `per_download` are already set per-download in `cli.add_options()` and must NOT be pushed globally.
- Every new test must fail before the implementation exists. No test may pass for the wrong reason.

---

## File Structure

| File | Responsibility |
|---|---|
| `dl/tomlio.py` (new) | Read `config.toml` as a tomlkit document, set values by path, write atomically. Raises `BrokenConfig` on a syntax error. |
| `dl/settings.py` (new) | The schema: `Field`, the section tuples, `parse`, `render`, `current`. Pure — no UI, no file I/O. |
| `dl/tui/settings.py` (new) | All screens: menu, schema-driven form, proxy, headers, categories. |
| `dl/tui/app.py` (modify) | `s` binding, `action_settings`, `reload_config`. |
| `pyproject.toml` (modify) | Add `tomlkit` dependency. |
| `tests/test_tomlio.py` (new) | Round-trip, comment preservation, atomic write, broken file. |
| `tests/test_settings.py` (new) | Schema validation, rendering, drift guard. |
| `tests/test_settings_screen.py` (new) | Pilot-driven screen tests. |
| `tests/test_app.py` (modify) | `reload_config` behaviour, `s` key. |
| `tests/test_integration.py` (modify) | Live save against a real aria2 daemon. |

---

### Task 1: TOML read/write that preserves comments

**Files:**
- Create: `dl/tomlio.py`
- Modify: `pyproject.toml`
- Test: `tests/test_tomlio.py`

**Interfaces:**
- Consumes: nothing
- Produces:
  - `class BrokenConfig(Exception)` with attribute `line: int`
  - `read(path: Path) -> tomlkit.TOMLDocument`
  - `set_value(doc, path: tuple[str, ...], value) -> None`
  - `drop(doc, path: tuple[str, ...]) -> None`
  - `write(path: Path, doc) -> None`
  - `apply(path: Path, changes: dict[tuple[str, ...], object]) -> None`

- [ ] **Step 1: Add the dependency**

Edit `pyproject.toml`, changing the `dependencies` line to:

```toml
dependencies = ["textual>=0.80", "tomlkit>=0.13"]
```

- [ ] **Step 2: Install it**

Run: `~/.local/share/dl/venv/bin/python -m pip install -q -e ".[dev]"`
Expected: completes silently. Verify with:
`~/.local/share/dl/venv/bin/python -c "import tomlkit; print(tomlkit.__version__)"`

- [ ] **Step 3: Write the failing tests**

Create `tests/test_tomlio.py`:

```python
import pytest

from dl import tomlio

SAMPLE = """\
[general]
theme = "aurora"

[proxy]
url = "http://127.0.0.1:2080"
# my note: needed here
domains = ["youtube.com"]
"""


def config(tmp_path, text=SAMPLE):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_setting_a_value_keeps_the_comment_beside_it(tmp_path):
    """The whole reason tomlkit is a dependency."""
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("proxy", "domains"), ["youtube.com", "github.com"])
    tomlio.write(path, doc)
    after = path.read_text()
    assert "# my note: needed here" in after
    assert "github.com" in after


def test_setting_a_value_keeps_key_order(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "ember")
    tomlio.write(path, doc)
    lines = [line for line in path.read_text().splitlines() if line.startswith("[")]
    assert lines == ["[general]", "[proxy]"]


def test_setting_a_value_changes_only_that_value(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "matrix")
    tomlio.write(path, doc)
    assert 'theme = "matrix"' in path.read_text()
    assert 'url = "http://127.0.0.1:2080"' in path.read_text()


def test_a_missing_section_is_created(tmp_path):
    path = config(tmp_path, "[general]\ntheme = \"aurora\"\n")
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("hooks", "on_complete"), "~/bin/x.sh")
    tomlio.write(path, doc)
    assert "[hooks]" in path.read_text()
    assert 'on_complete = "~/bin/x.sh"' in path.read_text()


def test_a_nested_table_is_created(tmp_path):
    path = config(tmp_path, "[general]\ntheme = \"aurora\"\n")
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("headers", "e.com", "Referer"), "https://e.com/")
    tomlio.write(path, doc)
    reread = tomlio.read(path)
    assert reread["headers"]["e.com"]["Referer"] == "https://e.com/"


def test_dropping_a_key_removes_it(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.drop(doc, ("proxy", "url"))
    tomlio.write(path, doc)
    assert "url" not in path.read_text()
    assert "domains" in path.read_text()


def test_dropping_a_key_that_is_not_there_is_quiet(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.drop(doc, ("proxy", "nope"))
    tomlio.write(path, doc)


def test_a_syntax_error_is_reported_with_its_line(tmp_path):
    """config.load() silently falls back to defaults on a broken file. Saving
    those defaults over it would destroy the user's config."""
    path = config(tmp_path, '[general]\ntheme = "aurora\n')
    with pytest.raises(tomlio.BrokenConfig) as exc:
        tomlio.read(path)
    assert exc.value.line >= 1


def test_a_failed_write_leaves_the_original_untouched(tmp_path, monkeypatch):
    path = config(tmp_path)
    original = path.read_text()
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "ember")

    def explode(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(tomlio.Path, "replace", explode)
    with pytest.raises(OSError):
        tomlio.write(path, doc)
    assert path.read_text() == original


def test_apply_writes_every_change_at_once(tmp_path):
    path = config(tmp_path)
    tomlio.apply(path, {("general", "theme"): "ember", ("limits", "splits"): 8})
    text = path.read_text()
    assert 'theme = "ember"' in text
    assert "splits = 8" in text
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_tomlio.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tomlio'`

- [ ] **Step 5: Write the implementation**

Create `dl/tomlio.py`:

```python
from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError


class BrokenConfig(Exception):
    """The file did not parse. Editing it blind would overwrite whatever the
    user actually wrote with whatever defaults the app fell back to."""

    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


def read(path: Path):
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except ParseError as exc:
        raise BrokenConfig(str(exc), getattr(exc, "line", 1)) from None


def set_value(doc, path: tuple[str, ...], value) -> None:
    table = doc
    for key in path[:-1]:
        if key not in table:
            table[key] = tomlkit.table()
        table = table[key]
    table[path[-1]] = value


def drop(doc, path: tuple[str, ...]) -> None:
    table = doc
    for key in path[:-1]:
        if key not in table:
            return
        table = table[key]
    if path[-1] in table:
        del table[path[-1]]


def write(path: Path, doc) -> None:
    staging = path.with_suffix(path.suffix + ".writing")
    staging.write_text(tomlkit.dumps(doc), encoding="utf-8")
    staging.replace(path)


def apply(path: Path, changes: dict[tuple[str, ...], object]) -> None:
    doc = read(path)
    for where, value in changes.items():
        set_value(doc, where, value)
    write(path, doc)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_tomlio.py -q`
Expected: PASS, 10 tests.

If `test_a_failed_write_leaves_the_original_untouched` errors because `tomlio.Path` is not patchable, the staging file was written but `.replace` was monkeypatched on the class — confirm `from pathlib import Path` is imported at module level in `dl/tomlio.py` so `tomlio.Path` resolves.

- [ ] **Step 7: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add dl/tomlio.py tests/test_tomlio.py pyproject.toml
git commit -m "dl: read and write config.toml without losing comments"
```

---

### Task 2: The settings schema

**Files:**
- Create: `dl/settings.py`
- Test: `tests/test_settings.py`

**Interfaces:**
- Consumes: `dl.config` (`Config`, `parse_duration`), `dl.destinations.ensure_writable`
- Produces:
  - `class Field` — frozen dataclass with `path: tuple[str, ...]`, `label: str`, `kind: str`, `choices: tuple[str, ...] = ()`, `help: str = ""`, `live: bool = False`
  - `class Invalid(ValueError)`
  - `GENERAL, LIMITS, YOUTUBE, HOOKS, CATEGORY_FIELDS: tuple[Field, ...]`
  - `LIST_SECTIONS: frozenset[str]`
  - `parse(field: Field, raw: str) -> object` — raises `Invalid`
  - `render(value) -> str`
  - `current(cfg: Config, field: Field) -> object`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings.py`:

```python
import dataclasses

import pytest

from dl import config, settings


def field(**over):
    base = dict(path=("limits", "splits"), label="Splits", kind="int")
    base.update(over)
    return settings.Field(**base)


def test_every_field_accepts_its_own_current_value(sandbox_cfg):
    """A schema that rejects the running config would block every save.

    sandbox_cfg, not defaults(): validating a path field creates the folder,
    and defaults() points at the real ~/Downloads.
    """
    for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS):
        for entry in section:
            shown = settings.render(settings.current(sandbox_cfg, entry))
            settings.parse(entry, shown)


def test_a_field_that_may_be_empty_accepts_empty():
    """No hook and no cookie browser are both legitimate settings."""
    for entry in settings.HOOKS + settings.YOUTUBE:
        if entry.allow_empty:
            assert settings.parse(entry, "") == ""


def test_the_two_optional_fields_are_the_only_ones_that_may_be_empty():
    optional = [
        f.path
        for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS)
        for f in section
        if f.allow_empty
    ]
    assert sorted(optional) == [("hooks", "on_complete"), ("youtube", "cookies_from")]


def test_an_int_field_rejects_words():
    with pytest.raises(settings.Invalid) as exc:
        settings.parse(field(), "banana")
    assert "number" in str(exc.value).lower()


def test_an_int_field_rejects_zero_and_below():
    with pytest.raises(settings.Invalid):
        settings.parse(field(), "0")


def test_an_int_field_accepts_a_positive_number():
    assert settings.parse(field(), "12") == 12


def test_a_choice_field_rejects_something_not_offered():
    entry = field(kind="choice", choices=("aurora", "ember"))
    with pytest.raises(settings.Invalid) as exc:
        settings.parse(entry, "neon")
    assert "aurora" in str(exc.value)


def test_a_choice_field_accepts_an_offered_value():
    entry = field(kind="choice", choices=("aurora", "ember"))
    assert settings.parse(entry, "ember") == "ember"


@pytest.mark.parametrize("raw", ["500K", "2M", "1G", "off", "1024"])
def test_a_rate_field_accepts_the_spellings_aria2_takes(raw):
    assert settings.parse(field(kind="rate"), raw)


@pytest.mark.parametrize("raw", ["fast", "500KB", "-1", ""])
def test_a_rate_field_rejects_anything_else(raw):
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="rate"), raw)


def test_a_duration_field_uses_the_same_parser_as_the_config():
    assert settings.parse(field(kind="duration"), "10m") == "10m"


def test_a_duration_field_rejects_nonsense():
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="duration"), "soon")


def test_a_bool_field_reads_on_and_off():
    entry = field(kind="bool")
    assert settings.parse(entry, "on") is True
    assert settings.parse(entry, "off") is False


def test_a_path_field_expands_home_and_returns_text(tmp_path):
    entry = field(kind="path")
    got = settings.parse(entry, str(tmp_path / "somewhere"))
    assert str(tmp_path) in got


def test_a_path_field_rejects_somewhere_it_cannot_write(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(settings.Invalid):
            settings.parse(field(kind="path"), str(locked / "sub"))
    finally:
        locked.chmod(0o700)


def test_a_colour_field_wants_a_hex_value():
    assert settings.parse(field(kind="colour"), "#c678dd") == "#c678dd"
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="colour"), "purple")


def test_a_text_field_takes_anything_but_empty():
    assert settings.parse(field(kind="text"), " hello ") == "hello"
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="text"), "   ")


def test_bools_render_as_on_and_off():
    assert settings.render(True) == "on"
    assert settings.render(False) == "off"


def test_theme_and_icons_are_the_only_live_fields():
    live = [f.path for section in (settings.GENERAL, settings.LIMITS,
                                   settings.YOUTUBE, settings.HOOKS)
            for f in section if f.live]
    assert sorted(live) == [("general", "ascii_icons"), ("general", "theme")]


def test_every_config_setting_is_reachable_from_the_screen():
    """This config grew from 12 keys to 20 in a day. Without this the schema
    falls behind and a new setting is silently uneditable."""
    scalars = {
        f.path[-1]
        for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS)
        for f in section
    }
    known = scalars | settings.LIST_SECTIONS
    for holder in (config.Config, config.General, config.Limits):
        for entry in dataclasses.fields(holder):
            assert entry.name in known, f"{holder.__name__}.{entry.name} is not editable"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.settings'`

- [ ] **Step 3: Write the implementation**

Create `dl/settings.py`:

```python
import re
from dataclasses import dataclass
from pathlib import Path

from .config import Config, parse_duration
from .destinations import ensure_writable
from .theme import THEMES

_RATE = re.compile(r"^(\d+[KMG]?|off)$", re.IGNORECASE)
_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")

LIST_SECTIONS = frozenset({"categories", "domains", "headers", "proxy_domains"})


class Invalid(ValueError):
    pass


@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]
    label: str
    kind: str
    choices: tuple[str, ...] = ()
    help: str = ""
    live: bool = False
    allow_empty: bool = False


GENERAL = (
    Field(("general", "theme"), "Theme", "choice", tuple(THEMES), live=True),
    Field(("general", "ascii_icons"), "ASCII icons", "bool", live=True),
    Field(("general", "notify"), "Notifications", "bool"),
    Field(("general", "default_dir"), "Default folder", "path"),
    Field(("general", "idle_timeout"), "Daemon idle timeout", "duration"),
)

LIMITS = (
    Field(("general", "max_concurrent"), "Parallel downloads", "int"),
    Field(("limits", "connections"), "Connections per server", "int"),
    Field(("limits", "splits"), "Segments per file", "int"),
    Field(("limits", "min_split"), "Smallest segment", "rate"),
    Field(("limits", "per_download"), "Speed cap per download", "rate"),
)

YOUTUBE = (
    Field(("youtube", "cookies_from"), "Cookies from browser", "text", allow_empty=True),
    Field(("youtube", "probe_timeout"), "Probe timeout", "duration"),
)

HOOKS = (
    Field(("hooks", "on_complete"), "Run after each download", "text", allow_empty=True),
    Field(("hooks", "timeout"), "Hook timeout", "duration"),
)

CATEGORY_FIELDS = (
    Field(("dir",), "Folder", "path"),
    Field(("ext",), "Extensions", "text"),
    Field(("icon",), "Icon", "text"),
    Field(("hue",), "Colour", "colour"),
)

ATTRIBUTE = {
    ("general", "max_concurrent"): lambda cfg: cfg.general.max_concurrent,
    ("general", "theme"): lambda cfg: cfg.general.theme,
    ("general", "ascii_icons"): lambda cfg: cfg.general.ascii_icons,
    ("general", "notify"): lambda cfg: cfg.general.notify,
    ("general", "default_dir"): lambda cfg: str(cfg.general.default_dir),
    ("general", "idle_timeout"): lambda cfg: f"{cfg.general.idle_timeout}s",
    ("limits", "connections"): lambda cfg: cfg.limits.connections,
    ("limits", "splits"): lambda cfg: cfg.limits.splits,
    ("limits", "min_split"): lambda cfg: cfg.limits.min_split,
    ("limits", "per_download"): lambda cfg: cfg.limits.per_download or "off",
    ("youtube", "cookies_from"): lambda cfg: cfg.cookies_from,
    ("youtube", "probe_timeout"): lambda cfg: f"{cfg.probe_timeout}s",
    ("hooks", "on_complete"): lambda cfg: cfg.on_complete,
    ("hooks", "timeout"): lambda cfg: f"{cfg.hook_timeout}s",
}


def current(cfg: Config, field: Field):
    return ATTRIBUTE[field.path](cfg)


def render(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def parse(field: Field, raw: str):
    text = raw.strip()
    if field.kind == "choice":
        if text not in field.choices:
            raise Invalid(f"pick one of: {', '.join(field.choices)}")
        return text
    if field.kind == "bool":
        if text not in ("on", "off"):
            raise Invalid("on or off")
        return text == "on"
    if field.kind == "int":
        if not text.isdigit() or int(text) < 1:
            raise Invalid("a whole number, 1 or more")
        return int(text)
    if field.kind == "rate":
        if not _RATE.match(text):
            raise Invalid("a size like 500K, 2M, 1G — or off")
        return text
    if field.kind == "duration":
        try:
            parse_duration(text)
        except ValueError:
            raise Invalid("a duration like 30s, 10m, 2h") from None
        return text
    if field.kind == "colour":
        if not _COLOUR.match(text):
            raise Invalid("a hex colour like #c678dd")
        return text
    if field.kind == "path":
        where = Path(text).expanduser()
        if not ensure_writable(where):
            raise Invalid(f"cannot write to {where}")
        return str(where)
    if not text and not field.allow_empty:
        raise Invalid("cannot be empty")
    return text
```

`allow_empty` exists because "" is a real setting for two fields: no completion
hook, and no browser to borrow cookies from. Without it the schema would reject
`dl`'s own defaults and block every save.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings.py -q`
Expected: PASS, 20 tests.

If `test_every_config_setting_is_reachable_from_the_screen` fails naming `proxy` or `cookies_from`, the schema uses the TOML key (`url`, `cookies_from`) while `Config` uses its own attribute name. Add the missing name to the schema or to `LIST_SECTIONS` — do not weaken the assertion. `proxy` (the URL) belongs on the Proxy screen from Task 6; add `"proxy"` to `LIST_SECTIONS` with a comment saying the Proxy screen owns it.

- [ ] **Step 5: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dl/settings.py tests/test_settings.py
git commit -m "dl: declare the settings schema"
```

---

### Task 3: Reloading config into a running app

**Files:**
- Modify: `dl/tui/app.py`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `dl.config.Config`, `dl.theme.select`
- Produces: `DlApp.reload_config(cfg: Config) -> None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
async def test_reload_swaps_the_theme_everywhere(cfg):
    from dl import config as config_module

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        changed = config_module.replace(
            cfg, general=config_module.replace(cfg.general, theme="ember")
        )
        app.reload_config(changed)
        await pilot.pause()
        assert app.cfg is changed
        assert app.theme_data is app.table.theme_data
        assert app.theme_data is app.status.theme_data
        assert app.theme_data is app.completed.theme_data


async def test_reload_pushes_a_changed_concurrency_to_the_daemon(cfg):
    from dl import config as config_module

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        changed = config_module.replace(
            cfg, general=config_module.replace(cfg.general, max_concurrent=7)
        )
        app.reload_config(changed)
    assert client.global_options["max-concurrent-downloads"] == "7"


async def test_reload_leaves_the_daemon_alone_when_concurrency_is_unchanged(cfg):
    """The other limits are set per-download at queue time; pushing them
    globally would change behaviour for downloads dl did not queue."""
    from dl import config as config_module

    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        changed = config_module.replace(
            cfg, limits=config_module.replace(cfg.limits, connections=4)
        )
        app.reload_config(changed)
    assert client.global_options == {}


async def test_reload_survives_a_daemon_that_is_gone(cfg):
    """A settings save must not fail because aria2 is down."""
    from dl import config as config_module
    from dl.rpc import Aria2Unreachable

    class Refusing(FakeClient):
        def change_global_option(self, options):
            raise Aria2Unreachable("gone")

    client = Refusing()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        changed = config_module.replace(
            cfg, general=config_module.replace(cfg.general, max_concurrent=9)
        )
        app.reload_config(changed)
        await pilot.pause()
        assert app.is_running is True
        assert app.cfg is changed
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_app.py -q -k reload`
Expected: FAIL — `AttributeError: 'DlApp' object has no attribute 'reload_config'`

- [ ] **Step 3: Write the implementation**

In `dl/tui/app.py`, add this method to `DlApp`, directly after `check_action`:

```python
    def reload_config(self, cfg: Config) -> None:
        """Adopt a freshly read config.

        Only max_concurrent reaches the daemon. The rest of the limits are set
        per-download at queue time, so pushing them globally would change
        behaviour for downloads dl did not queue.
        """
        was = self.cfg
        self.cfg = cfg
        self.theme_data = theme.select(cfg)
        for widget in (self.status, self.table, self.completed):
            widget.theme_data = self.theme_data
        self.table.refresh_view()
        if cfg.general.max_concurrent != was.general.max_concurrent:
            try:
                self.client.change_global_option(
                    {"max-concurrent-downloads": str(cfg.general.max_concurrent)}
                )
            except (Aria2Error, Aria2Unreachable) as exc:
                self.notify(f"saved, but the daemon did not take it: {exc}", severity="warning")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_app.py -q -k reload`
Expected: PASS, 4 tests.

- [ ] **Step 5: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dl/tui/app.py tests/test_app.py
git commit -m "dl: let a running dashboard adopt a new config"
```

---

### Task 4: The schema-driven form screen

**Files:**
- Create: `dl/tui/settings.py`
- Test: `tests/test_settings_screen.py`

**Interfaces:**
- Consumes: `dl.settings` (`Field`, `parse`, `render`, `current`, `Invalid`)
- Produces:
  - `class FormScreen(ModalScreen[dict])` — constructed as
    `FormScreen(title: str, fields: tuple[Field, ...], cfg: Config)`.
    Dismisses with `{}` on cancel, or `{path: value}` of changed fields on accept.
  - Attributes for tests: `.values: dict[tuple, object]`, `.body: str`, `.error: str`, `.field: int`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_settings_screen.py`:

```python
import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from dl import config, settings
from dl.tui.settings import FormScreen


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class Host(App):
    CSS = """
    FormScreen { align: center middle; }
    #settings-box { width: 76; padding: 1 2; }
    #settings-list, #settings-error { height: auto; }
    """

    def __init__(self, screen):
        super().__init__()
        self._screen = screen
        self.result = "unset"

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self):
        self.push_screen(self._screen, lambda value: setattr(self, "result", value))


def form(cfg, fields=settings.LIMITS):
    return FormScreen("Limits", fields, cfg)


async def test_it_shows_every_field_with_its_current_value(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "Connections per server" in screen.body
        assert str(cfg.limits.connections) in screen.body


async def test_escape_returns_no_changes(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}


async def test_arrows_cycle_a_choice_field(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = screen.values[("general", "theme")]
        await pilot.press("right")
        await pilot.pause()
        assert screen.values[("general", "theme")] != before


async def test_arrows_toggle_a_bool_field(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("down")
        before = screen.values[("general", "ascii_icons")]
        await pilot.press("right")
        await pilot.pause()
        assert screen.values[("general", "ascii_icons")] is not before


async def test_enter_opens_an_editor_on_a_text_field(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
        assert screen.editing is True


async def test_a_typed_value_is_kept(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "9"
        await pilot.press("enter")
        await pilot.pause()
        assert screen.values[("general", "max_concurrent")] == 9
        assert screen.editing is False


async def test_a_bad_value_is_refused_with_a_reason(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "banana"
        await pilot.press("enter")
        await pilot.pause()
        assert "number" in screen.error.lower()
        assert screen.editing is True


async def test_escape_while_editing_cancels_only_the_edit(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        screen.input_value = "9"
        await pilot.press("escape")
        await pilot.pause()
        assert screen.editing is False
        assert app.result == "unset", "the screen itself must stay open"


async def test_saving_returns_only_what_changed(cfg):
    screen = FormScreen("General", settings.GENERAL, cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("right")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert list(app.result) == [("general", "theme")]


async def test_saving_with_nothing_changed_returns_nothing(cfg):
    screen = form(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui.settings'`

- [ ] **Step 3: Write the implementation**

Create `dl/tui/settings.py`:

```python
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Static

from .. import settings
from ..config import Config

HINT = "↑↓ field   ←→ change   ⏎ edit   ^S save   esc cancel"
CYCLED = ("choice", "bool")


class FormScreen(ModalScreen[dict]):
    """One screen for any list of scalar settings.

    Dismisses with the fields that changed, so the caller writes only those and
    a save never rewrites values the user did not touch.
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous_field", "up", priority=True),
        Binding("down", "next_field", "down", priority=True),
        Binding("left", "previous_value", "left", priority=True),
        Binding("right", "next_value", "right", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, title: str, fields: tuple[settings.Field, ...], cfg: Config):
        super().__init__()
        self.title_text = title
        self.fields = fields
        self.cfg = cfg
        self.original = {f.path: settings.current(cfg, f) for f in fields}
        self.values = dict(self.original)
        self.field = 0
        self.editing = False
        self.body = ""
        self.error = ""

    @property
    def input_value(self) -> str:
        return self.query_one("#settings-input", Input).value

    @input_value.setter
    def input_value(self, value: str) -> None:
        self.query_one("#settings-input", Input).value = value

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static(f"  ⚙  {self.title_text}", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input")
            yield Static("", id="settings-error")
            yield Static(HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _current(self) -> settings.Field:
        return self.fields[self.field]

    def _repaint(self) -> None:
        rows = []
        for index, entry in enumerate(self.fields):
            marker = "▌" if index == self.field else " "
            shown = settings.render(self.values[entry.path])
            wrap = f"‹ {shown} ›" if entry.kind in CYCLED else shown
            rows.append(f"{marker} {entry.label:<24} {wrap}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def _move(self, delta: int) -> None:
        if self.editing:
            return
        self.field = (self.field + delta) % len(self.fields)
        self.error = ""
        self._repaint()

    def action_next_field(self) -> None:
        self._move(1)

    def action_previous_field(self) -> None:
        self._move(-1)

    def _cycle(self, delta: int) -> None:
        if self.editing:
            return
        entry = self._current()
        if entry.kind == "bool":
            self.values[entry.path] = not self.values[entry.path]
        elif entry.kind == "choice":
            options = entry.choices
            at = options.index(self.values[entry.path]) if self.values[entry.path] in options else 0
            self.values[entry.path] = options[(at + delta) % len(options)]
        else:
            return
        self._preview(entry)
        self._repaint()

    def action_next_value(self) -> None:
        self._cycle(1)

    def action_previous_value(self) -> None:
        self._cycle(-1)

    def _preview(self, entry: settings.Field) -> None:
        """Live fields are shown at once so they can be judged, but they are
        still only previews until the save."""
        if entry.live:
            self.app.reload_config(self._provisional())

    def _provisional(self) -> Config:
        from dataclasses import replace

        general = replace(
            self.cfg.general,
            theme=self.values.get(("general", "theme"), self.cfg.general.theme),
            ascii_icons=self.values.get(("general", "ascii_icons"), self.cfg.general.ascii_icons),
        )
        return replace(self.cfg, general=general)

    def action_edit(self) -> None:
        entry = self._current()
        if entry.kind in CYCLED:
            return
        if not self.editing:
            self.editing = True
            box = self.query_one("#settings-input", Input)
            box.display = True
            box.value = settings.render(self.values[entry.path])
            box.focus()
            return
        self._commit(entry)

    def _commit(self, entry: settings.Field) -> None:
        try:
            self.values[entry.path] = settings.parse(entry, self.input_value)
        except settings.Invalid as exc:
            self.error = f"  ⚠  {entry.label}: {exc}"
            self._repaint()
            return
        self._close_editor()

    def _close_editor(self) -> None:
        self.editing = False
        self.error = ""
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._commit(self._current())

    def action_cancel(self) -> None:
        if self.editing:
            self._close_editor()
            return
        if any(f.live for f in self.fields):
            self.app.reload_config(self.cfg)
        self.dismiss({})

    def action_save(self) -> None:
        if self.editing:
            self._commit(self._current())
            return
        self.dismiss(
            {path: value for path, value in self.values.items() if value != self.original[path]}
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q`
Expected: PASS, 10 tests.

`Host` in these tests has no `reload_config`, so live-field tests would raise. If `test_arrows_cycle_a_choice_field` fails with `AttributeError: 'Host' object has no attribute 'reload_config'`, add this method to `Host` in the test file:

```python
    def reload_config(self, cfg):
        self.reloaded = cfg
```

- [ ] **Step 5: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add dl/tui/settings.py tests/test_settings_screen.py
git commit -m "dl: add the schema-driven settings form"
```

---

### Task 5: The settings menu, the `s` key, and the broken-config guard

**Files:**
- Modify: `dl/tui/settings.py`
- Modify: `dl/tui/app.py`
- Test: `tests/test_settings_screen.py`, `tests/test_app.py`

**Interfaces:**
- Consumes: `FormScreen` (Task 4), `dl.tomlio` (Task 1), `DlApp.reload_config` (Task 3)
- Produces:
  - `class SettingsMenuScreen(ModalScreen[None])` — constructed as `SettingsMenuScreen(cfg: Config, config_file: Path)`
  - `DlApp.action_settings()`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_screen.py`:

```python
from pathlib import Path

from dl.tui.settings import SettingsMenuScreen

SAMPLE = """\
[general]
theme = "aurora"
max_concurrent = 3
"""


def menu(cfg, tmp_path, text=SAMPLE):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return SettingsMenuScreen(cfg, path), path


async def test_the_menu_lists_every_section(cfg, tmp_path):
    screen, _ = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        for name in ("General", "Limits", "YouTube", "Hooks", "Proxy", "Headers", "Categories"):
            assert name in screen.body


async def test_a_broken_config_refuses_to_be_edited(cfg, tmp_path):
    """config.load() falls back to defaults on a broken file, so saving would
    write those defaults over whatever the user actually wrote."""
    screen, _ = menu(cfg, tmp_path, '[general]\ntheme = "aurora\n')
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "syntax error" in screen.body.lower()
        assert screen.blocked is True


async def test_a_broken_config_is_never_written_to(cfg, tmp_path):
    broken = '[general]\ntheme = "aurora\n'
    screen, path = menu(cfg, tmp_path, broken)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("enter")
        await pilot.pause()
    assert path.read_text() == broken


async def test_escape_closes_the_menu(cfg, tmp_path):
    screen, _ = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert app.result is None


async def test_saving_a_section_writes_it_to_the_file(cfg, tmp_path):
    screen, path = menu(cfg, tmp_path)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.save({("general", "theme"): "ember"})
        await pilot.pause()
    assert 'theme = "ember"' in path.read_text()
```

Append to `tests/test_app.py`:

```python
async def test_s_opens_the_settings_menu(cfg):
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("s")
        await pilot.pause()
        assert type(app.screen).__name__ == "SettingsMenuScreen"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py tests/test_app.py -q -k "menu or settings or broken"`
Expected: FAIL — `ImportError: cannot import name 'SettingsMenuScreen'`

- [ ] **Step 3: Add the menu screen**

Append to `dl/tui/settings.py`:

```python
from pathlib import Path

from .. import config as config_module
from .. import tomlio

SECTIONS = (
    ("General", settings.GENERAL),
    ("Limits", settings.LIMITS),
    ("YouTube", settings.YOUTUBE),
    ("Hooks", settings.HOOKS),
)
LIST_ROWS = ("Proxy", "Headers", "Categories")
MENU_HINT = "↑↓ move   ⏎ open   esc close"


class SettingsMenuScreen(ModalScreen[None]):
    BINDINGS = [
        ("escape", "close", "close"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("enter", "open", "open", priority=True),
    ]

    def __init__(self, cfg: Config, config_file: Path):
        super().__init__()
        self.cfg = cfg
        self.config_file = config_file
        self.cursor = 0
        self.body = ""
        self.blocked = False
        self.problem = ""

    @property
    def rows(self) -> tuple[str, ...]:
        return tuple(name for name, _ in SECTIONS) + LIST_ROWS

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("  ⚙  Settings", id="settings-head")
            yield Static("", id="settings-list")
            yield Static(MENU_HINT, id="settings-hint")

    def on_mount(self) -> None:
        try:
            tomlio.read(self.config_file)
        except tomlio.BrokenConfig as exc:
            self.blocked = True
            self.problem = (
                f"  ⚠  config.toml has a syntax error on line {exc.line} —\n"
                f"     fix it before editing here. dl is running on defaults\n"
                f"     until it parses."
            )
        except OSError:
            pass
        self._repaint()

    def _repaint(self) -> None:
        if self.blocked:
            self.body = self.problem
        else:
            rows = []
            for index, name in enumerate(self.rows):
                marker = "▌" if index == self.cursor else " "
                arrow = "  ›" if name in LIST_ROWS else ""
                rows.append(f"{marker} {name}{arrow}")
            self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)

    def _move(self, delta: int) -> None:
        if self.blocked:
            return
        self.cursor = (self.cursor + delta) % len(self.rows)
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_open(self) -> None:
        if self.blocked:
            return
        name = self.rows[self.cursor]
        for label, fields in SECTIONS:
            if label == name:
                self.app.push_screen(FormScreen(label, fields, self.cfg), self._saved)
                return

    def _saved(self, changes: dict | None) -> None:
        if changes:
            self.save(changes)

    def save(self, changes: dict) -> None:
        try:
            tomlio.apply(self.config_file, changes)
        except (OSError, tomlio.BrokenConfig) as exc:
            self.app.notify(f"could not save settings: {exc}", severity="error")
            return
        self.cfg = config_module.load(self.config_file)
        self.app.reload_config(self.cfg)
        self.app.notify("settings saved")

    def action_close(self) -> None:
        self.dismiss(None)
```

- [ ] **Step 4: Wire the `s` key**

In `dl/tui/app.py`, add to `DlApp.BINDINGS`, after the `("r", "retry", "retry")` line:

```python
        ("s", "settings", "settings"),
```

Add this method to `DlApp`, directly after `action_add`:

```python
    def action_settings(self) -> None:
        from .settings import SettingsMenuScreen

        self.push_screen(SettingsMenuScreen(self.cfg, CONFIG_FILE))
```

Add `CONFIG_FILE` to the config import at the top of `dl/tui/app.py`:

```python
from ..config import CONFIG_FILE, STATE_DIR, Config
```

Update the `HINT` constant in `dl/tui/app.py` to mention it:

```python
HINT = (
    "a add   space pause/resume   d delete   J K reorder   l limit   "
    "o open   f finder   s settings   tab completed   q quit"
)
```

- [ ] **Step 5: Add the CSS**

In `dl/tui/app.py`, extend the `CSS` constant. Add `SettingsMenuScreen, FormScreen` to the modal alignment rule, `#settings-box` to the box rule, and append:

```css
#settings-list, #settings-error { height: auto; }
#settings-head { text-style: bold; }
#settings-input { margin-top: 1; }
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py tests/test_app.py -q`
Expected: PASS.

`Host` in `tests/test_settings_screen.py` needs the CSS for the new screens and a `reload_config`; extend its `CSS` string with `SettingsMenuScreen { align: center middle; }` if the menu renders at zero height.

- [ ] **Step 7: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dl/tui/settings.py dl/tui/app.py tests/test_settings_screen.py tests/test_app.py
git commit -m "dl: open settings with s, refuse to edit a broken config"
```

---

### Task 6: The Proxy screen

**Files:**
- Modify: `dl/tui/settings.py`
- Test: `tests/test_settings_screen.py`

**Interfaces:**
- Consumes: `FormScreen`, `tomlio`
- Produces: `class ProxyScreen(ModalScreen[dict])` — constructed as `ProxyScreen(cfg: Config)`, dismisses `{}` or `{("proxy","url"): str, ("proxy","domains"): list[str]}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_screen.py`:

```python
from dl.tui.settings import ProxyScreen


def proxied(cfg, domains=("youtube.com",)):
    return config.replace(cfg, proxy_domains=tuple(domains))


async def test_the_proxy_screen_shows_url_and_domains(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert cfg.proxy in screen.body
        assert "youtube.com" in screen.body


async def test_a_domain_can_be_added(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.pause()
        assert "github.com" in screen.domains


async def test_a_blank_domain_is_refused(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("   ")
        await pilot.pause()
        assert screen.domains == ["youtube.com"]
        assert screen.error


async def test_a_domain_can_be_deleted(cfg):
    screen = ProxyScreen(proxied(cfg, ("youtube.com", "github.com")))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.cursor = 1
        screen.delete_selected()
        await pilot.pause()
        assert screen.domains == ["youtube.com"]


async def test_the_url_itself_can_be_changed(cfg):
    """Displaying it without a way to change it would leave the one setting
    the whole screen is named after uneditable."""
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("u")
        await pilot.pause()
        assert screen.editing == "url"
        screen.query_one("#settings-input", Input).value = "http://127.0.0.1:1080"
        await pilot.press("enter")
        await pilot.pause()
        assert screen.url == "http://127.0.0.1:1080"


async def test_saving_returns_url_and_domains(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result[("proxy", "domains")] == ["youtube.com", "github.com"]
    assert app.result[("proxy", "url")] == cfg.proxy


async def test_escape_returns_nothing(cfg):
    screen = ProxyScreen(proxied(cfg))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_domain("github.com")
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q -k proxy`
Expected: FAIL — `ImportError: cannot import name 'ProxyScreen'`

- [ ] **Step 3: Write the implementation**

Append to `dl/tui/settings.py`:

```python
LIST_HINT = "↑↓ move   a add   d delete   ⏎ edit   u url   ^S save   esc cancel"


class ProxyScreen(ModalScreen[dict]):
    """The proxy URL, and the hosts always sent through it."""

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("a", "add", "add", priority=True),
        Binding("d", "delete", "delete", priority=True),
        Binding("u", "edit_url", "url", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.url = cfg.proxy
        self.domains = list(cfg.proxy_domains)
        self.cursor = 0
        self.editing = ""
        self.body = ""
        self.error = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("  ⚙  Proxy", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input")
            yield Static("", id="settings-error")
            yield Static(LIST_HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _repaint(self) -> None:
        rows = [f"  URL   {self.url}", ""]
        if not self.domains:
            rows.append("  (no domains — every download goes direct unless -p)")
        for index, domain in enumerate(self.domains):
            marker = "▌" if index == self.cursor else " "
            rows.append(f"{marker} {domain}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def add_domain(self, value: str) -> None:
        text = value.strip().lower()
        if not text:
            self.error = "  ⚠  a domain cannot be empty"
            self._repaint()
            return
        if text in self.domains:
            self.error = f"  ⚠  {text} is already listed"
            self._repaint()
            return
        self.domains.append(text)
        self.cursor = len(self.domains) - 1
        self.error = ""
        self._repaint()

    def delete_selected(self) -> None:
        if not self.domains:
            return
        del self.domains[self.cursor]
        self.cursor = max(0, min(self.cursor, len(self.domains) - 1))
        self._repaint()

    def _open_editor(self, mode: str, prefill: str) -> None:
        self.editing = mode
        box = self.query_one("#settings-input", Input)
        box.display = True
        box.value = prefill
        box.focus()

    def _close_editor(self) -> None:
        self.editing = ""
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def action_add(self) -> None:
        if not self.editing:
            self._open_editor("add", "")

    def action_edit_url(self) -> None:
        if not self.editing:
            self._open_editor("url", self.url)

    def action_edit(self) -> None:
        if self.editing:
            self._submit()
            return
        if self.domains:
            self._open_editor("edit", self.domains[self.cursor])

    def action_delete(self) -> None:
        if not self.editing:
            self.delete_selected()

    def _submit(self) -> None:
        value = self.query_one("#settings-input", Input).value
        if self.editing == "url":
            text = value.strip()
            if not text:
                self.error = "  ⚠  the proxy needs a URL"
                self._repaint()
                return
            self.url = text
        elif self.editing == "add":
            self.add_domain(value)
        elif self.editing == "edit" and self.domains:
            text = value.strip().lower()
            if not text:
                self.error = "  ⚠  a domain cannot be empty"
                self._repaint()
                return
            self.domains[self.cursor] = text
        self._close_editor()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self._submit()

    def _move(self, delta: int) -> None:
        if self.editing or not self.domains:
            return
        self.cursor = (self.cursor + delta) % len(self.domains)
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        if self.editing:
            self._close_editor()
            return
        self.dismiss({})

    def action_save(self) -> None:
        if self.editing:
            self._submit()
            return
        self.dismiss({("proxy", "url"): self.url, ("proxy", "domains"): list(self.domains)})
```

- [ ] **Step 4: Open it from the menu**

In `SettingsMenuScreen.action_open`, replace the method body's trailing `return` block with:

```python
    def action_open(self) -> None:
        if self.blocked:
            return
        name = self.rows[self.cursor]
        for label, fields in SECTIONS:
            if label == name:
                self.app.push_screen(FormScreen(label, fields, self.cfg), self._saved)
                return
        if name == "Proxy":
            self.app.push_screen(ProxyScreen(self.cfg), self._saved)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q`
Expected: PASS.

- [ ] **Step 6: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add dl/tui/settings.py tests/test_settings_screen.py
git commit -m "dl: edit the proxy and its domains from settings"
```

---

### Task 7: The Headers screen

**Files:**
- Modify: `dl/tui/settings.py`
- Test: `tests/test_settings_screen.py`

**Interfaces:**
- Consumes: `tomlio`
- Produces: `class HeadersScreen(ModalScreen[dict])` — `HeadersScreen(cfg: Config)`, dismisses `{}` or `{("headers",): {host: {key: value}}}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_screen.py`:

```python
from dl.tui.settings import HeadersScreen

RULES = {"indllserver.info": {"Referer": "https://indllserver.info/", "User-Agent": "Mozilla/5.0"}}


async def test_headers_are_shown_as_flat_rows(cfg):
    """Two levels of TOML nesting, one flat list — no sub-screen to drill into."""
    screen = HeadersScreen(config.replace(cfg, headers=RULES))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(screen.rules) == 2
        assert "indllserver.info" in screen.body
        assert "Referer" in screen.body


async def test_a_rule_can_be_added(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | X-Token | abc")
        await pilot.pause()
        assert ("e.com", "X-Token", "abc") in screen.rules


async def test_a_rule_needs_all_three_parts(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | X-Token")
        await pilot.pause()
        assert screen.rules == []
        assert "host | key | value" in screen.error


async def test_a_rule_can_be_deleted(cfg):
    screen = HeadersScreen(config.replace(cfg, headers=RULES))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.cursor = 0
        screen.delete_selected()
        await pilot.pause()
        assert len(screen.rules) == 1


async def test_saving_rebuilds_the_nesting(cfg):
    screen = HeadersScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_rule("e.com | Referer | https://e.com/")
        screen.add_rule("e.com | X-Token | abc")
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result[("headers",)] == {
        "e.com": {"Referer": "https://e.com/", "X-Token": "abc"}
    }


async def test_a_header_value_is_shown_but_never_logged(cfg):
    """Cookie and Authorization live here; the row shows the value because you
    are editing it, but nothing else may repeat it."""
    screen = HeadersScreen(config.replace(cfg, headers={"e.com": {"Cookie": "s=SECRET"}}))
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "SECRET" in screen.body
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q -k header`
Expected: FAIL — `ImportError: cannot import name 'HeadersScreen'`

- [ ] **Step 3: Write the implementation**

Append to `dl/tui/settings.py`:

```python
HEADER_HINT = "↑↓ move   a add   d delete   ^S save   esc cancel"
HEADER_FORM = "host | key | value"


class HeadersScreen(ModalScreen[dict]):
    """Per-host request headers.

    TOML nests these two deep. The editor keeps them flat — one row per
    (host, key, value) — and rebuilds the nesting on save, so there is no
    second level to drill into for what is usually one line per site.
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("a", "add", "add", priority=True),
        Binding("d", "delete", "delete", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.rules = [
            (host, key, value)
            for host, fields in cfg.headers.items()
            for key, value in fields.items()
        ]
        self.cursor = 0
        self.editing = False
        self.body = ""
        self.error = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("  ⚙  Headers", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input", placeholder=HEADER_FORM)
            yield Static("", id="settings-error")
            yield Static(HEADER_HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _repaint(self) -> None:
        rows = []
        if not self.rules:
            rows.append("  (no header rules)")
        for index, (host, key, value) in enumerate(self.rules):
            marker = "▌" if index == self.cursor else " "
            rows.append(f"{marker} {host:<26} {key:<16} {value}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def add_rule(self, raw: str) -> None:
        parts = [piece.strip() for piece in raw.split("|")]
        if len(parts) != 3 or not all(parts):
            self.error = f"  ⚠  write it as {HEADER_FORM}"
            self._repaint()
            return
        host, key, value = parts
        self.rules.append((host.lower(), key, value))
        self.cursor = len(self.rules) - 1
        self.error = ""
        self._repaint()

    def delete_selected(self) -> None:
        if not self.rules:
            return
        del self.rules[self.cursor]
        self.cursor = max(0, min(self.cursor, len(self.rules) - 1))
        self._repaint()

    def _close_editor(self) -> None:
        self.editing = False
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def action_add(self) -> None:
        if self.editing:
            return
        self.editing = True
        box = self.query_one("#settings-input", Input)
        box.display = True
        box.value = ""
        box.focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.add_rule(self.query_one("#settings-input", Input).value)
        if not self.error:
            self._close_editor()

    def action_delete(self) -> None:
        if not self.editing:
            self.delete_selected()

    def _move(self, delta: int) -> None:
        if self.editing or not self.rules:
            return
        self.cursor = (self.cursor + delta) % len(self.rules)
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        if self.editing:
            self._close_editor()
            return
        self.dismiss({})

    def action_save(self) -> None:
        if self.editing:
            return
        nested: dict[str, dict[str, str]] = {}
        for host, key, value in self.rules:
            nested.setdefault(host, {})[key] = value
        self.dismiss({("headers",): nested})
```

- [ ] **Step 4: Open it from the menu**

In `SettingsMenuScreen.action_open`, after the `Proxy` branch, add:

```python
        if name == "Headers":
            self.app.push_screen(HeadersScreen(self.cfg), self._saved)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q`
Expected: PASS.

- [ ] **Step 6: Verify the whole-table write works**

`tomlio.set_value(doc, ("headers",), {...})` replaces the entire `[headers]` table. Confirm with:

```bash
~/.local/share/dl/venv/bin/python -c "
from pathlib import Path
import tempfile
from dl import tomlio
p = Path(tempfile.mkdtemp()) / 'c.toml'
p.write_text('[general]\ntheme = \"aurora\"\n\n[headers.\"a.com\"]\nX = \"1\"\n')
tomlio.apply(p, {('headers',): {'b.com': {'Y': '2'}}})
print(p.read_text())
"
```
Expected: `[general]` and its value intact, `headers` now containing only `b.com`.

- [ ] **Step 7: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dl/tui/settings.py tests/test_settings_screen.py
git commit -m "dl: edit per-host headers from settings"
```

---

### Task 8: The Categories screen

**Files:**
- Modify: `dl/tui/settings.py`
- Test: `tests/test_settings_screen.py`

**Interfaces:**
- Consumes: `FormScreen`, `settings.CATEGORY_FIELDS`
- Produces: `class CategoriesScreen(ModalScreen[dict])` — `CategoriesScreen(cfg: Config)`, dismisses `{}` or `{("categories",): {name: {"dir": str, "ext": list[str], "icon": str, "hue": str}}}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_settings_screen.py`:

```python
from dl.tui.settings import CategoriesScreen


async def test_every_category_is_listed(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert len(screen.names) == len(cfg.categories)
        assert "video" in screen.body


async def test_a_category_can_be_added(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_category("books")
        await pilot.pause()
        assert "books" in screen.names


async def test_a_duplicate_category_is_refused(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_category("video")
        await pilot.pause()
        assert screen.names.count("video") == 1
        assert screen.error


async def test_a_category_can_be_deleted(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        before = len(screen.names)
        screen.cursor = 0
        screen.delete_selected()
        await pilot.pause()
        assert len(screen.names) == before - 1


async def test_extensions_are_edited_as_a_comma_separated_string(cfg):
    """A list inside a record inside a list. One text field beats a third
    level of drilling for values that are three letters long."""
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert screen.shown_ext("video") == ", ".join(cfg.categories["video"].ext)


async def test_saving_returns_every_category_with_split_extensions(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.apply_edit("video", {("ext",): "mkv, mp4"})
        await pilot.press("ctrl+s")
        await pilot.pause()
    assert app.result[("categories",)]["video"]["ext"] == ["mkv", "mp4"]


async def test_escape_returns_nothing(cfg):
    screen = CategoriesScreen(cfg)
    app = Host(screen)
    async with app.run_test() as pilot:
        await pilot.pause()
        screen.add_category("books")
        await pilot.press("escape")
        await pilot.pause()
    assert app.result == {}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q -k categor`
Expected: FAIL — `ImportError: cannot import name 'CategoriesScreen'`

- [ ] **Step 3: Write the implementation**

Append to `dl/tui/settings.py`:

```python
CATEGORY_HINT = "↑↓ move   a add   d delete   ⏎ edit   ^S save   esc cancel"


class CategoriesScreen(ModalScreen[dict]):
    """The categories that decide where a file lands.

    The built-in eight have no special status: config.load() already merges
    user categories over the defaults, so one can be edited or removed like
    any other.
    """

    BINDINGS = [
        ("escape", "cancel", "cancel"),
        Binding("up", "previous", "up", priority=True),
        Binding("down", "next", "down", priority=True),
        Binding("a", "add", "add", priority=True),
        Binding("d", "delete", "delete", priority=True),
        Binding("enter", "edit", "edit", priority=True),
        Binding("ctrl+s", "save", "save", priority=True),
    ]

    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg
        self.entries = {
            name: {
                "dir": str(category.dir),
                "ext": list(category.ext),
                "icon": category.icon,
                "hue": category.hue,
            }
            for name, category in cfg.categories.items()
        }
        self.cursor = 0
        self.adding = False
        self.body = ""
        self.error = ""

    @property
    def names(self) -> list[str]:
        return list(self.entries)

    def shown_ext(self, name: str) -> str:
        return ", ".join(self.entries[name]["ext"])

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("  ⚙  Categories", id="settings-head")
            yield Static("", id="settings-list")
            yield Input("", id="settings-input", placeholder="new category name")
            yield Static("", id="settings-error")
            yield Static(CATEGORY_HINT, id="settings-hint")

    def on_mount(self) -> None:
        self.query_one("#settings-input", Input).display = False
        self._repaint()

    def _repaint(self) -> None:
        rows = []
        for index, name in enumerate(self.names):
            marker = "▌" if index == self.cursor else " "
            entry = self.entries[name]
            rows.append(f"{marker} {entry['icon']} {name:<12} {entry['dir']}")
        self.body = "\n".join(rows)
        self.query_one("#settings-list", Static).update(self.body)
        self.query_one("#settings-error", Static).update(self.error)

    def add_category(self, raw: str) -> None:
        name = raw.strip().lower()
        if not name:
            self.error = "  ⚠  a category needs a name"
            self._repaint()
            return
        if name in self.entries:
            self.error = f"  ⚠  {name} already exists"
            self._repaint()
            return
        self.entries[name] = {
            "dir": str(self.cfg.general.default_dir / name),
            "ext": [],
            "icon": "📥",
            "hue": "#8a8a8a",
        }
        self.cursor = len(self.entries) - 1
        self.error = ""
        self._repaint()

    def delete_selected(self) -> None:
        if not self.entries:
            return
        del self.entries[self.names[self.cursor]]
        self.cursor = max(0, min(self.cursor, len(self.entries) - 1))
        self._repaint()

    def apply_edit(self, name: str, changes: dict) -> None:
        for path, value in changes.items():
            key = path[-1]
            self.entries[name][key] = (
                [piece.strip() for piece in str(value).split(",") if piece.strip()]
                if key == "ext"
                else value
            )
        self._repaint()

    def _close_editor(self) -> None:
        self.adding = False
        box = self.query_one("#settings-input", Input)
        box.display = False
        self.set_focus(None)
        self._repaint()

    def action_add(self) -> None:
        if self.adding:
            return
        self.adding = True
        box = self.query_one("#settings-input", Input)
        box.display = True
        box.value = ""
        box.focus()

    def on_input_submitted(self, _event: Input.Submitted) -> None:
        self.add_category(self.query_one("#settings-input", Input).value)
        if not self.error:
            self._close_editor()

    def action_delete(self) -> None:
        if not self.adding:
            self.delete_selected()

    def action_edit(self) -> None:
        if self.adding or not self.entries:
            return
        name = self.names[self.cursor]
        entry = self.entries[name]
        # The form reads its starting values from ATTRIBUTE, which only knows
        # top-level settings, so a category's four are seeded directly.
        screen = FormScreen(f"Category — {name}", settings.CATEGORY_FIELDS, self.cfg)
        screen.values = {
            ("dir",): entry["dir"],
            ("ext",): ", ".join(entry["ext"]),
            ("icon",): entry["icon"],
            ("hue",): entry["hue"],
        }
        screen.original = dict(screen.values)
        self.app.push_screen(screen, lambda changes: self.apply_edit(name, changes or {}))

    def _move(self, delta: int) -> None:
        if self.adding or not self.entries:
            return
        self.cursor = (self.cursor + delta) % len(self.entries)
        self._repaint()

    def action_next(self) -> None:
        self._move(1)

    def action_previous(self) -> None:
        self._move(-1)

    def action_cancel(self) -> None:
        if self.adding:
            self._close_editor()
            return
        self.dismiss({})

    def action_save(self) -> None:
        if self.adding:
            return
        self.dismiss({("categories",): {name: dict(entry) for name, entry in self.entries.items()}})
```

- [ ] **Step 4: Make FormScreen tolerate seeded values**

`FormScreen.__init__` calls `settings.current(cfg, f)` for every field, which raises `KeyError` for a category's `("dir",)`. Change those two lines in `dl/tui/settings.py` to:

```python
        self.original = {
            f.path: settings.current(cfg, f) for f in fields if f.path in settings.ATTRIBUTE
        }
        self.values = dict(self.original)
```

`ATTRIBUTE` is already public from Task 2, so no rename is needed.

- [ ] **Step 5: Open it from the menu**

In `SettingsMenuScreen.action_open`, after the `Headers` branch, add:

```python
        if name == "Categories":
            self.app.push_screen(CategoriesScreen(self.cfg), self._saved)
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_settings_screen.py -q`
Expected: PASS.

- [ ] **Step 7: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add dl/tui/settings.py dl/settings.py tests/test_settings_screen.py
git commit -m "dl: edit categories from settings"
```

---

### Task 9: Live verification and documentation

**Files:**
- Modify: `tests/test_integration.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above

- [ ] **Step 1: Write the failing live test**

Append to `tests/test_integration.py`:

```python
def test_a_saved_concurrency_reaches_a_real_daemon(env, tmp_path):
    """Everything else is checked against fakes. This asserts aria2 itself
    takes the new value on a running daemon."""
    from dl import tomlio

    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    before = client._call("aria2.getGlobalOption")["max-concurrent-downloads"]

    config_file = tmp_path / "config.toml"
    config.write_default(config_file)
    tomlio.apply(config_file, {("general", "max_concurrent"): 9})
    assert config.load(config_file).general.max_concurrent == 9

    client.change_global_option({"max-concurrent-downloads": "9"})
    after = client._call("aria2.getGlobalOption")["max-concurrent-downloads"]
    assert after == "9"
    assert after != before


def test_the_written_default_config_survives_a_round_trip(tmp_path):
    """dl ships a heavily commented default. A save must not eat it."""
    from dl import tomlio

    path = tmp_path / "config.toml"
    config.write_default(path)
    comments = [line for line in path.read_text().splitlines() if line.startswith("#")]
    tomlio.apply(path, {("general", "theme"): "ember"})
    after = path.read_text()
    assert 'theme = "ember"' in after
    for line in comments:
        assert line in after
```

- [ ] **Step 2: Run the live tests**

Run: `~/.local/share/dl/venv/bin/python -m pytest tests/test_integration.py -q -k "concurrency or round_trip"`
Expected: PASS, 2 tests. These need `aria2c` installed; the module skips without it.

- [ ] **Step 3: Drive the real screen once by hand**

Run: `dl`

Check by eye, since only an eye can:
- `s` opens Settings; the seven sections are listed
- General → `→` on Theme recolours the dashboard behind the modal immediately
- `esc` puts the original colours back
- General → Theme → `^S` saves; the dashboard keeps the new colour
- `grep theme ~/.config/dl/config.toml` shows the new value and the file still has its comments
- Limits → `⏎` on Parallel downloads, type `banana`, `⏎` → refused with a reason, still editable
- Headers → `a` → `e.com | Referer | https://e.com/` → `^S`, then check the file has a proper `[headers."e.com"]` table

- [ ] **Step 4: Document it**

In `README.md`, add `s` to the key table, next to the other dashboard keys:

```markdown
| `s` | settings | | | |
```

And add this after the configuration table:

```markdown
`s` in the dashboard opens the same settings as a screen: sections for general,
limits, YouTube and hooks, plus list editors for proxy domains, per-host headers
and categories. Theme and icons preview as you change them; everything else
applies on `^S`. Saving rewrites only the values you touched, so the comments and
layout of your `config.toml` survive.

Nothing needs a restart. Most settings are read when they are next used, and
`max_concurrent` is pushed to the running daemon.

If `config.toml` has a syntax error, the settings screen refuses to open rather
than overwrite it — `dl` runs on defaults until the file parses, and saving those
defaults would destroy what you wrote.
```

- [ ] **Step 5: Run the whole suite**

Run: `~/.local/share/dl/venv/bin/python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_integration.py README.md
git commit -m "dl: verify settings against a real daemon, document the screen"
```

---

## Self-Review Notes

**Spec coverage:** every section maps to a task — schema (2), tomlkit write-back (1), the four form sections (4, 5), proxy (6), headers (7), categories (8), reload and daemon push (3), broken-config guard (5), drift guard (2), live verification (9).

**Defects this review caught and fixed:**

- `parse` rejected `""`, which is the shipped default for `on_complete` and `cookies_from` — the schema would have rejected `dl`'s own config and blocked every save. Fixed with `Field.allow_empty`, plus a test pinning that exactly two fields carry it.
- `test_every_field_accepts_its_own_current_value` used `config.defaults()`, and validating a path field calls `ensure_writable`, which creates the folder — the test would have created `~/Downloads` and the other real category folders on any machine running it. Switched to `sandbox_cfg`.
- `ProxyScreen` displayed the proxy URL but gave no way to change it, leaving the one setting the screen is named after uneditable. Added `u` plus a test.
- `replace_category_cfg` was a function that took two arguments and returned the first unchanged. Deleted; the comment it was hiding behind now sits at the call site.

**Known rough edge, deliberately left in Task 8:** `FormScreen` was designed for top-level settings and is reused for a category's four fields by seeding `values` and `original` directly. If that seam proves ugly in review, the alternative is a separate `RecordFormScreen` — but duplicating the whole screen is worse than the seam, so try the seam first.

**Not covered, by decision:** no `dl settings` subcommand, no search, no reset-to-defaults, no import/export, and no watching `config.toml` for external edits.
