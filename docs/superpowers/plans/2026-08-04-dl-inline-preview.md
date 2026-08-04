# `dl` Inline Preview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dl <url>` attach a live, scoped dashboard for the downloads it just queued, detachable with Ctrl-C, exiting with a printed summary when they finish.

**Architecture:** `PreviewApp` subclasses the existing `DlApp` and overrides three small extension points, inheriting all rendering, modals, and error handling. `DlApp` gains those extension points in a behaviour-preserving refactor. Formatting of the exit summary is a pure function with no Textual or I/O.

**Tech Stack:** Python ≥3.11, `textual` (already a dependency), `pytest`. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-04-dl-inline-preview-design.md`

## Global Constraints

- **No new dependencies.** `textual` at runtime, `pytest` for dev — nothing else.
- **No test may touch the network.** Bind `127.0.0.1` on an ephemeral port or use `tmp_path`.
- **aria2 JSON-RPC returns numbers as strings.** Always `int()` `totalLength`, `completedLength`, `downloadSpeed`, `connections` at the boundary.
- **Comments: write none by default.** Repo `CLAUDE.md` mandates minimal comments — no restating code, no banners. Short docstrings only on public API surface.
- **Emoji only in fixed 2-cell columns**, never inline in prose. Degrade to `[ok]`/`[fail]`/`[...]` when `theme.icons` is False.
- **A disconnect must never be read as completion.** An empty `tell_active` caused by `Aria2Unreachable` must not exit the preview. This is the one real correctness trap.
- **Preview attaches only when `sys.stdout.isatty()`** and `--no-preview` was not passed.
- Existing behaviour of `dl` with no arguments, and of every subcommand, is unchanged.

## File Structure

| Path | Responsibility |
|---|---|
| `downloader/dl/cli.py` | `cmd_add` returns `(rc, gids)` |
| `downloader/dl/tui/app.py` | three extension points on `DlApp`, behaviour unchanged |
| `downloader/dl/tui/preview.py` | **new** — `summarise`, `PreviewApp`, `run_preview` |
| `downloader/dl/__main__.py` | `--no-preview`, attach decision, print summary |
| `downloader/tests/test_preview.py` | **new** |
| `downloader/tests/test_cli.py` | updated for the `(rc, gids)` tuple |
| `downloader/tests/test_app.py` | extension points preserve behaviour |
| `downloader/tests/test_main.py` | dispatch decisions |
| `downloader/tests/test_integration.py` | real download attaches and exits |
| `downloader/README.md`, `README.md` | document the preview |

---

## Task 1: `cmd_add` returns the gids

**Files:**
- Modify: `downloader/dl/cli.py:35`
- Modify: `downloader/dl/__main__.py:97`
- Test: `downloader/tests/test_cli.py`

**Interfaces:**
- Consumes: nothing new
- Produces: `cmd_add(urls: list[str], cfg: Config, client, explicit_dir: Path | None) -> tuple[int, list[str]]` — gids in argument order, `[]` when nothing queued

- [ ] **Step 1: Update the existing assertions to the tuple shape**

In `downloader/tests/test_cli.py`, replace every `cmd_add` call site. The seven affected tests become:

```python
def test_cmd_add_queues_each_url(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso", "https://e.com/b.mkv"], cfg, client, None)
    assert rc == 0
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
        rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, locked / "sub")
        assert rc == 1
        assert gids == []
        assert "cannot write" in capsys.readouterr().err
        assert not client.added
    finally:
        locked.chmod(0o700)


def test_cmd_add_with_no_urls_is_an_error(cfg, capsys):
    rc, gids = cli.cmd_add([], cfg, FakeClient(), None)
    assert rc == 1
    assert gids == []
    assert capsys.readouterr().err


def test_cmd_add_refuses_a_mistyped_subcommand_instead_of_downloading_it(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["limit off"], cfg, client, None)
    assert rc == 1
    assert gids == []
    err = capsys.readouterr().err
    assert "not a URL" in err
    assert "--help" in err
    assert not client.added


def test_cmd_add_rejects_the_whole_batch_if_any_entry_is_not_a_url(cfg, capsys):
    client = FakeClient()
    rc, gids = cli.cmd_add(["https://e.com/a.iso", "oops"], cfg, client, None)
    assert rc == 1
    assert gids == []
    assert not client.added
```

- [ ] **Step 2: Add the new contract tests**

Append to `downloader/tests/test_cli.py`:

```python
def test_cmd_add_returns_gids_in_argument_order(cfg):
    client = FakeClient()
    rc, gids = cli.cmd_add(
        ["https://e.com/a.iso", "https://e.com/b.mkv", "https://e.com/c.zip"], cfg, client, None
    )
    assert rc == 0
    assert gids == ["gid1", "gid2", "gid3"]


def test_cmd_add_skips_gids_for_unwritable_destinations(cfg, tmp_path, capsys):
    client = FakeClient()
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        rc, gids = cli.cmd_add(["https://e.com/a.iso"], cfg, client, locked / "sub")
        assert (rc, gids) == (1, [])
    finally:
        locked.chmod(0o700)
    capsys.readouterr()
```

- [ ] **Step 3: Run to verify they fail**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_cli.py -x`
Expected: FAIL — `TypeError: cannot unpack non-sequence int`

- [ ] **Step 4: Change the implementation**

In `downloader/dl/cli.py`, replace the whole `cmd_add` function with:

```python
def cmd_add(
    urls: list[str], cfg: Config, client, explicit_dir: Path | None
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
    for url in urls:
        name = routing.filename_from_url(url)
        resolution = routing.resolve(url, name, cfg, explicit_dir)
        if not _ensure_writable(resolution.path):
            print(f"dl: cannot write to {resolution.path}", file=sys.stderr)
            failures += 1
            continue
        gids.append(client.add_uri([url], add_options(cfg, resolution)))
        print(f"  {resolution.category.icon} queued  {name or url}  →  {resolution.path}")
    return (1 if failures else 0), gids
```

- [ ] **Step 5: Update the only production caller**

In `downloader/dl/__main__.py`, replace line 97:

```python
        return cli.cmd_add(urls, cfg, client, explicit_dir)
```

with:

```python
        rc, _gids = cli.cmd_add(urls, cfg, client, explicit_dir)
        return rc
```

- [ ] **Step 6: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS — all tests green, no behaviour change yet

- [ ] **Step 7: Commit**

```bash
git add downloader/dl/cli.py downloader/dl/__main__.py downloader/tests/test_cli.py
git commit -m "dl: return queued gids from cmd_add"
```

---

## Task 2: Extension points on `DlApp`

**Files:**
- Modify: `downloader/dl/tui/app.py:96-111`
- Test: `downloader/tests/test_app.py`

**Interfaces:**
- Consumes: nothing new
- Produces, on `DlApp`:
  - `_filter_items(items: list[dict]) -> list[dict]` — identity in the base class
  - `splash_when_empty: bool` — class attribute, `True` in the base class
  - `_after_refresh(items: list[dict]) -> None` — no-op in the base class, called **only** after a successful poll

Behaviour of `DlApp` is unchanged. `_after_refresh` sitting after the disconnect guard is what makes a dropped daemon structurally incapable of looking like completion.

- [ ] **Step 1: Write tests pinning both the hooks and the unchanged behaviour**

Append to `downloader/tests/test_app.py`:

```python
async def test_filter_items_is_identity_by_default(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        items = [{"gid": "x"}, {"gid": "y"}]
        assert app._filter_items(items) == items


async def test_base_app_shows_splash_when_empty(cfg):
    client = FakeClient()
    client.active = []
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.splash_when_empty is True
        assert "d o w n l o a d e r" in app.table.text or app.table.rows == []


async def test_after_refresh_runs_on_a_successful_poll(cfg):
    seen = []

    class Probe(DlApp):
        def _after_refresh(self, items):
            seen.append(len(items))

    app = Probe(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert seen and seen[-1] == 2


async def test_after_refresh_is_skipped_when_the_daemon_is_unreachable(cfg):
    from dl.rpc import Aria2Unreachable

    seen = []

    class Dead(DlApp):
        def _after_refresh(self, items):
            seen.append(items)

    class DeadClient(FakeClient):
        def tell_active(self):
            raise Aria2Unreachable("gone")

    app = Dead(cfg, DeadClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        seen.clear()
        await app.refresh_data()
        assert seen == []
        assert app.disconnected is True


async def test_filter_items_narrows_what_reaches_the_table(cfg):
    class OnlyG2(DlApp):
        def _filter_items(self, items):
            return [i for i in items if i["gid"] == "g2"]

    app = OnlyG2(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert [r.gid for r in app.table.rows] == ["g2"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_app.py -x`
Expected: FAIL — `AttributeError: 'DlApp' object has no attribute '_filter_items'`

- [ ] **Step 3: Add the class attribute**

In `downloader/dl/tui/app.py`, inside `class DlApp(App):` immediately after the `BINDINGS` list closing bracket, add:

```python
    splash_when_empty = True
```

- [ ] **Step 4: Replace `refresh_data` with the hooked version**

Replace lines 96–111 of `downloader/dl/tui/app.py` (the whole `refresh_data` method) with:

```python
    def _filter_items(self, items: list[dict]) -> list[dict]:
        return items

    def _after_refresh(self, items: list[dict]) -> None:
        return None

    async def refresh_data(self) -> None:
        try:
            polled = list(self.client.tell_active()) + list(self.client.tell_waiting())
            stat = self.client.get_global_stat()
        except (Aria2Unreachable, Aria2Error):
            self.disconnected = True
            self.status.update(f"[{self.theme_data.danger}]⚠ daemon lost — reconnecting[/]")
            return
        self.disconnected = False
        items = self._filter_items(polled)
        self.table.set_rows([row_from_status(item, self.cfg) for item in items])
        elapsed = int(time.monotonic() - self.started)
        self.status.update_stats(stats_from(stat, self.limit, elapsed))
        if not items and self.splash_when_empty and not self.showing_completed:
            self.table.update(
                f"[{self.theme_data.accent}]{SPLASH}[/]\n   press a to add a download"
            )
        self._after_refresh(items)
```

- [ ] **Step 5: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS — including every pre-existing app test, proving the refactor changed no behaviour

- [ ] **Step 6: Commit**

```bash
git add downloader/dl/tui/app.py downloader/tests/test_app.py
git commit -m "dl: add refresh extension points to the dashboard app"
```

---

## Task 3: The summary formatter

**Files:**
- Create: `downloader/dl/tui/preview.py`
- Test: `downloader/tests/test_preview.py`

**Interfaces:**
- Consumes: `dl.format.human_bytes`, `dl.format.human_duration`, `dl.format.human_speed`
- Produces:
  - `Result` — a `TypedDict`-shaped plain dict with keys `name: str`, `status: str`, `bytes: int`, `seconds: int`, `error: str`
  - `summarise(results: list[dict], icons: bool = True) -> list[str]`

`summarise` is pure: no I/O, no Textual, no clock. Statuses `active` and `waiting` mean still-running and are collapsed into one trailing line.

- [ ] **Step 1: Write the failing test**

Create `downloader/tests/test_preview.py`:

```python
from dl.tui.preview import summarise


def result(**over):
    base = {"name": "a.iso", "status": "complete", "bytes": 6127219712, "seconds": 683, "error": ""}
    base.update(over)
    return base


def test_summarise_empty_is_empty():
    assert summarise([]) == []


def test_summarise_success_shows_size_duration_and_average():
    line = summarise([result()])[0]
    assert "✅" in line
    assert "a.iso" in line
    assert "5.7 GB" in line
    assert "11m 23s" in line
    assert "8.6 MB/s" in line


def test_summarise_success_without_duration_omits_the_average():
    line = summarise([result(seconds=0)])[0]
    assert "5.7 GB" in line
    assert "/s" not in line


def test_summarise_error_shows_the_message():
    line = summarise([result(status="error", error="HTTP 403")])[0]
    assert "❌" in line
    assert "a.iso" in line
    assert "HTTP 403" in line


def test_summarise_error_without_message_says_failed():
    assert "failed" in summarise([result(status="error", error="")])[0]


def test_summarise_removed_is_reported():
    line = summarise([result(status="removed")])[0]
    assert "a.iso" in line
    assert "removed" in line


def test_summarise_running_collapses_into_one_trailing_line():
    lines = summarise([result(status="active"), result(name="b.mkv", status="waiting")])
    assert len(lines) == 1
    assert "2 still downloading" in lines[0]
    assert "dl ls" in lines[0]


def test_summarise_singular_wording_for_one_running():
    assert "1 still downloading" in summarise([result(status="active")])[0]


def test_summarise_mixed_lists_finished_then_running():
    lines = summarise(
        [result(), result(name="b.mkv", status="error", error="boom"), result(name="c.zip", status="active")]
    )
    assert len(lines) == 3
    assert "a.iso" in lines[0]
    assert "b.mkv" in lines[1]
    assert "1 still downloading" in lines[2]


def test_summarise_ascii_mode_uses_no_emoji():
    lines = summarise(
        [result(), result(name="b.mkv", status="error", error="boom"), result(name="c.zip", status="active")],
        icons=False,
    )
    joined = " ".join(lines)
    assert "✅" not in joined and "❌" not in joined and "⏳" not in joined
    assert "[ok]" in joined
    assert "[fail]" in joined
    assert "[...]" in joined


def test_summarise_never_emits_markup_that_would_break_a_terminal():
    for line in summarise([result(), result(status="active")]):
        assert "\x1b[" not in line
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -x`
Expected: FAIL — `ModuleNotFoundError: No module named 'dl.tui.preview'`

- [ ] **Step 3: Implement**

Create `downloader/dl/tui/preview.py`:

```python
from ..format import human_bytes, human_duration, human_speed

RUNNING = ("active", "waiting")
MARKS = {
    True: {"ok": "✅", "fail": "❌", "wait": "⏳"},
    False: {"ok": "[ok]", "fail": "[fail]", "wait": "[...]"},
}


def summarise(results: list[dict], icons: bool = True) -> list[str]:
    mark = MARKS[bool(icons)]
    lines: list[str] = []
    running = 0
    for item in results:
        status = item.get("status", "")
        if status in RUNNING:
            running += 1
            continue
        name = item.get("name") or "(unnamed)"
        if status == "complete":
            size = human_bytes(int(item.get("bytes", 0) or 0))
            seconds = int(item.get("seconds", 0) or 0)
            detail = size
            if seconds > 0:
                average = human_speed(int(item.get("bytes", 0) or 0) // seconds)
                detail = f"{size} in {human_duration(seconds)}   avg {average}"
            lines.append(f"  {mark['ok']} {name}   {detail}")
        elif status == "removed":
            lines.append(f"  {mark['fail']} {name}   removed")
        else:
            lines.append(f"  {mark['fail']} {name}   {item.get('error') or 'failed'}")
    if running:
        lines.append(
            f"  {mark['wait']} {running} still downloading — `dl` to watch, `dl ls` to list"
        )
    return lines
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -v`
Expected: PASS — 11 tests

- [ ] **Step 5: Commit**

```bash
git add downloader/dl/tui/preview.py downloader/tests/test_preview.py
git commit -m "dl: add preview exit summary formatter"
```

---

## Task 4: `PreviewApp` and `run_preview`

**Files:**
- Modify: `downloader/dl/tui/preview.py`
- Test: `downloader/tests/test_preview.py`

**Interfaces:**
- Consumes: `DlApp` with `_filter_items` / `splash_when_empty` / `_after_refresh` (Task 2); `summarise` (Task 3)
- Produces:
  - `PreviewApp(cfg, client, gids: list[str])` — attributes `watch: set[str]`, `results: list[dict]`
  - `run_preview(cfg, client, gids: list[str]) -> list[str]`
  - `PREVIEW_HINT: str`

**Critical, verified by experiment:** Textual **merges** `BINDINGS` across the
MRO — a subclass cannot remove a parent's key by redeclaring a shorter list.
Declaring `BINDINGS` on `PreviewApp` would therefore leave `a`, `tab`, `J`, `K`
and `r` live, opening the add modal and switching to an unwired Completed tab.
The keys are dropped by overriding their **action methods** as no-ops instead,
and two tests pin that. Do not add a `BINDINGS` list to `PreviewApp`.

- [ ] **Step 1: Write the failing test**

Append to `downloader/tests/test_preview.py`:

```python
import pytest

from dl.rpc import Aria2Unreachable
from dl.tui.preview import PreviewApp


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def status(gid, state="active", **over):
    base = {
        "gid": gid,
        "status": state,
        "totalLength": "1000",
        "completedLength": "500",
        "downloadSpeed": "100",
        "connections": "4",
        "files": [{"path": f"/tmp/{gid}.iso", "uris": [{"uri": f"https://e.com/{gid}.iso"}]}],
        "errorMessage": "",
    }
    base.update(over)
    return base


class PreviewClient:
    def __init__(self, active=("g1", "g2"), waiting=()):
        self.active = [status(g) for g in active]
        self.waiting = [status(g, "waiting") for g in waiting]
        self.paused = []
        self.final = {}
        self.fail = False

    def tell_active(self):
        if self.fail:
            raise Aria2Unreachable("gone")
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        if self.fail:
            raise Aria2Unreachable("gone")
        return self.waiting

    def tell_stopped(self, offset=0, num=1000):
        return []

    def get_global_stat(self):
        if self.fail:
            raise Aria2Unreachable("gone")
        return {"downloadSpeed": "100", "numActive": "2", "numWaiting": "0", "numStopped": "0"}

    def tell_status(self, gid):
        return self.final.get(gid, status(gid, "complete", completedLength="1000"))

    def pause(self, gid):
        self.paused.append(gid)

    def unpause(self, gid):
        pass


async def test_preview_shows_only_the_watched_gids(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g1", "g3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert sorted(r.gid for r in app.table.rows) == ["g1", "g3"]


async def test_preview_pauses_only_the_selected_watched_gid(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g2"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("space")
        assert client.paused == ["g2"]


async def test_preview_pause_all_covers_only_the_watch_set(cfg):
    client = PreviewClient(active=("g1", "g2", "g3"))
    app = PreviewApp(cfg, client, ["g1", "g3"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("p")
        assert sorted(client.paused) == ["g1", "g3"]


async def test_preview_exits_once_every_watched_gid_settles(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
    assert app.is_running is False
    assert [r["status"] for r in app.results] == ["complete"]


async def test_preview_stays_while_one_gid_is_still_waiting(cfg):
    client = PreviewClient(active=("g1",), waiting=("g2",))
    app = PreviewApp(cfg, client, ["g1", "g2"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True


async def test_preview_does_not_exit_when_the_daemon_is_unreachable(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.fail = True
        await app.refresh_data()
        await pilot.pause()
        assert app.is_running is True
        assert app.disconnected is True
        assert app.results == []


async def test_preview_never_renders_the_splash(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g9"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.splash_when_empty is False
        assert "d o w n l o a d e r" not in app.table.text


async def test_preview_collects_error_results(cfg):
    client = PreviewClient(active=("g1",))
    client.final["g1"] = status("g1", "error", errorMessage="HTTP 403")
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        client.active = []
        await app.refresh_data()
        await pilot.pause()
    assert app.results[0]["status"] == "error"
    assert app.results[0]["error"] == "HTTP 403"


async def test_preview_hint_replaces_the_dashboard_hint(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "detach" in app.hint_text
        assert "add" not in app.hint_text


async def test_preview_ignores_the_add_key(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert len(app.screen_stack) == 1


async def test_preview_ignores_the_completed_tab_key(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.showing_completed is False


async def test_preview_ignores_the_reorder_keys(cfg):
    class Reorderable(PreviewClient):
        def __init__(self):
            super().__init__(active=("g1",))
            self.positions = []

        def change_position(self, gid, pos, how):
            self.positions.append(gid)
            return 0

    client = Reorderable()
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("J")
        await pilot.press("K")
        assert client.positions == []


async def test_preview_detach_leaves_running_results(cfg):
    client = PreviewClient(active=("g1",))
    app = PreviewApp(cfg, client, ["g1"])
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("q")
        await pilot.pause()
    assert app.results == [] or app.results[0]["status"] in ("active", "waiting")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -x`
Expected: FAIL — `ImportError: cannot import name 'PreviewApp'`

- [ ] **Step 3: Implement**

Append to `downloader/dl/tui/preview.py`:

```python
from pathlib import Path

from ..theme import select
from .app import DlApp

PREVIEW_HINT = (
    "space pause/resume   l limit   L limit this   o open   f finder   "
    "d delete   ^C detach"
)


class PreviewApp(DlApp):
    splash_when_empty = False

    def action_add(self) -> None:
        return None

    def action_toggle_tab(self) -> None:
        return None

    def action_move_down(self) -> None:
        return None

    def action_move_up(self) -> None:
        return None

    def action_retry(self) -> None:
        return None

    def __init__(self, cfg, client, gids: list[str]):
        super().__init__(cfg, client)
        self.watch = set(gids)
        self.results: list[dict] = []
        self.hint_text = PREVIEW_HINT

    def on_mount(self) -> None:
        super().on_mount()
        self.hint.update(PREVIEW_HINT)

    def _filter_items(self, items: list[dict]) -> list[dict]:
        return [item for item in items if item.get("gid") in self.watch]

    def _after_refresh(self, items: list[dict]) -> None:
        if items:
            return
        self.results = self._collect_results()
        self.exit()

    def _collect_results(self) -> list[dict]:
        collected = []
        for gid in self.watch:
            try:
                raw = self.client.tell_status(gid)
            except Exception:
                continue
            files = raw.get("files") or [{}]
            collected.append(
                {
                    "name": Path(files[0].get("path", "") or "").name or gid,
                    "status": raw.get("status", ""),
                    "bytes": int(raw.get("completedLength", 0) or 0),
                    "seconds": max(int(time.monotonic() - self.started), 0),
                    "error": raw.get("errorMessage", "") or "",
                }
            )
        return sorted(collected, key=lambda r: r["name"])


def run_preview(cfg, client, gids: list[str]) -> list[str]:
    app = PreviewApp(cfg, client, gids)
    app.run()
    return summarise(app.results, icons=select(cfg).icons)
```

Add `import time` to the top of the file, beside the existing imports.

- [ ] **Step 4: Run to verify it passes**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_preview.py -v`
Expected: PASS — all preview tests

- [ ] **Step 5: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add downloader/dl/tui/preview.py downloader/tests/test_preview.py
git commit -m "dl: add scoped preview app over the dashboard"
```

---

## Task 5: Dispatch, docs, and a real download

**Files:**
- Modify: `downloader/dl/__main__.py`
- Modify: `downloader/tests/test_main.py`
- Modify: `downloader/tests/test_integration.py`
- Modify: `downloader/README.md`, `README.md`

**Interfaces:**
- Consumes: `cmd_add -> (rc, gids)` (Task 1), `run_preview(cfg, client, gids) -> list[str]` (Task 4)
- Produces: final user-facing behaviour

- [ ] **Step 1: Write the failing dispatch tests**

Append to `downloader/tests/test_main.py`:

```python
class StubClient:
    def add_uri(self, uris, options):
        return "gidX"


def _wire(monkeypatch, tmp_path, isatty, calls):
    from dl import cli, config, daemon

    monkeypatch.setattr(entry.config, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(entry, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(daemon, "ensure_running", lambda *a, **k: StubClient())
    monkeypatch.setattr(daemon, "bump_generation", lambda *a, **k: 1)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (0, ["gidX"]))
    monkeypatch.setattr("sys.stdout.isatty", lambda: isatty)
    monkeypatch.setattr(entry, "run_preview", lambda *a, **k: calls.append(a) or ["  done"])


def test_url_with_a_tty_attaches_the_preview(monkeypatch, tmp_path, capsys):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert len(calls) == 1
    assert "done" in capsys.readouterr().out


def test_url_without_a_tty_does_not_attach(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, False, calls)
    assert entry.main(["https://e.com/a.iso"]) == 0
    assert calls == []


def test_no_preview_flag_suppresses_attachment(monkeypatch, tmp_path):
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    assert entry.main(["--no-preview", "https://e.com/a.iso"]) == 0
    assert calls == []


def test_no_preview_flag_is_not_treated_as_a_url(monkeypatch, tmp_path):
    from dl import cli

    seen = []
    calls = []
    _wire(monkeypatch, tmp_path, True, calls)

    def record(urls, *args, **kwargs):
        seen.append(urls)
        return 0, ["gidX"]

    monkeypatch.setattr(cli, "cmd_add", record)
    entry.main(["--no-preview", "https://e.com/a.iso"])
    assert seen == [["https://e.com/a.iso"]]


def test_failed_add_returns_one_without_attaching(monkeypatch, tmp_path):
    from dl import cli

    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (1, []))
    assert entry.main(["oops"]) == 1
    assert calls == []


def test_partial_add_still_attaches_for_the_successes(monkeypatch, tmp_path):
    from dl import cli

    calls = []
    _wire(monkeypatch, tmp_path, True, calls)
    monkeypatch.setattr(cli, "cmd_add", lambda *a, **k: (1, ["gidX"]))
    assert entry.main(["https://e.com/a.iso", "https://e.com/b.iso"]) == 1
    assert len(calls) == 1
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_main.py -x`
Expected: FAIL — `AttributeError: module 'dl.__main__' has no attribute 'run_preview'`

- [ ] **Step 3: Wire dispatch**

In `downloader/dl/__main__.py`, add to the imports:

```python
from .tui.preview import run_preview
```

Add to the `USAGE` string, immediately after the `dl -d <dir> <url>` line:

```
  --no-preview             queue and exit without attaching the live preview
```

Replace the `if urls:` block (the two lines added in Task 1) with:

```python
    if urls:
        daemon.bump_generation(config.STATE_DIR)
        rc, gids = cli.cmd_add(urls, cfg, client, explicit_dir)
        if gids and preview and sys.stdout.isatty():
            for line in run_preview(cfg, client, gids):
                print(line)
        return rc
```

And immediately after the `args` assignment at the top of `_run`, add the flag strip:

```python
    preview = "--no-preview" not in args
    args = [a for a in args if a != "--no-preview"]
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd downloader && ~/.local/share/dl/venv/bin/python -m pytest tests/test_main.py -v`
Expected: PASS

- [ ] **Step 5: Add the integration test**

Append to `downloader/tests/test_integration.py`:

`app.run(headless=True)` blocks with no timeout, so a stalled download would
hang the suite forever. Drive it through the pilot with a bounded wait instead:

```python
async def test_preview_exits_when_a_real_download_finishes(env, fileserver):
    import asyncio

    from dl.tui.preview import PreviewApp

    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    gid = queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    app = PreviewApp(cfg, client, [gid])
    async with app.run_test() as pilot:
        for _ in range(300):
            await app.refresh_data()
            if not app.is_running:
                break
            await asyncio.sleep(0.1)

    target = cfg.categories["iso"].dir / "sample.iso"
    assert target.exists() and target.stat().st_size == len(PAYLOAD)
    assert app.results and app.results[0]["status"] == "complete"
```

- [ ] **Step 6: Run the full suite**

Run: `cd downloader && make test`
Expected: PASS — including the new integration test

- [ ] **Step 7: Document it**

In `downloader/README.md`, under the Usage section, replace the sentence beginning "`dl <url>` returns immediately" with:

```markdown
`dl <url>` queues the download and attaches a live preview showing just those
files. Ctrl-C detaches — the downloads keep running — and the preview closes
itself with a one-line summary per file when they finish.

Piped or redirected output never attaches, so scripts and cron behave as before.
Pass `--no-preview` to skip it in an interactive shell.

Inside the preview: `space` pause/resume, `l` / `L` limit, `o` open, `f` reveal
in Finder, `d` delete, `↑` `↓` move, `Ctrl-C` detach.
```

Add `--no-preview` to the usage block in the same file, and to the `dl` section of the root `README.md`.

- [ ] **Step 8: Verify by hand**

```bash
cd downloader && make install
dl --no-preview https://speed.hetzner.de/100MB.bin   # queues, exits immediately
dl https://speed.hetzner.de/100MB.bin                 # preview attaches
```

Expected: the first returns to the prompt at once; the second shows a live card, `space` pauses it, Ctrl-C returns the prompt and `dl ls` still shows it running.

- [ ] **Step 9: Commit**

```bash
git add downloader/dl/__main__.py downloader/tests/test_main.py downloader/tests/test_integration.py downloader/README.md README.md
git commit -m "dl: attach a live preview to queued downloads"
```

---

## Self-Review Notes

**Spec coverage.** §1 flow → Tasks 1 and 5; the `cmd_add` contract change → Task 1; §2 keymap and hint → Task 4; the global status bar → inherited unchanged, no task needed; §2 summary output → Task 3; §3 `PreviewApp`/`summarise`/`run_preview` → Tasks 3 and 4; §3 splash suppression → Tasks 2 and 4; §4 disconnect-is-not-completion → Task 2 (structural: `_after_refresh` sits after the disconnect guard) and Task 4 (behavioural test); §5 every listed test → Tasks 1, 3, 4, 5.

**Bug caught while writing this plan.** The first draft dropped `a`, `tab`,
`J`/`K` and `r` by declaring a shorter `BINDINGS` list on `PreviewApp`. Running
a Textual experiment showed bindings are **merged** across the MRO, so those
keys would all have stayed live. Task 4 now drops them by overriding the action
methods, with three tests pinning it.

**Deviation from the spec.** The spec described `PreviewApp` overriding `refresh_data` directly. Doing so would duplicate the disconnect handling, and a future edit to one copy would silently diverge — exactly the failure the subclassing approach was chosen to avoid. Task 2 instead adds three extension points to `DlApp` so the override is three small methods and the disconnect guard exists in one place only. The observable behaviour is identical.

**Duration caveat.** `seconds` is measured from when the preview attached, not from when aria2 first opened the connection. Because the preview attaches immediately after queuing, the two are the same in practice; for a download already in flight the elapsed time would read low. Not worth extra state to correct.
