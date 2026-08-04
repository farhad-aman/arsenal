# `dl` Interactive Path Picker Design

**Date:** 2026-08-04
**Status:** Approved, ready for implementation planning
**Builds on:** `2026-08-04-dl-downloader-design.md`, `2026-08-04-dl-inline-preview-design.md`

## Summary

`dl <url>` currently routes a download to its filetype's folder with no way to
intervene short of `-d`. This adds an interactive destination picker that opens
before anything is queued: `Enter` accepts the routed default, or you choose
another folder — including one you type — and only then does the download start.

The picker is the first screen of the Textual session that was already going to
open for the live preview, so the terminal is taken over once, and accepting the
default costs a single keystroke.

## Goals

- Choose the destination per download, with the routed folder preselected
- One keystroke for the common case
- Offer folders you actually use, learned from existing history
- Type any path, existing or not
- Never interfere with scripts, `-d`, or `--no-preview`

## Non-goals

- A full directory browser (arrow into subfolders, `..`, create-folder key)
- Remembering a choice back into `config.toml` as a new routing rule
- Changing the destination after a download has started
- A picker for `dl watch` or `dl -f`

---

## 1. Flow

```
dl https://.../movie.mkv https://.../ubuntu.iso
   |
   +- ensure_running()
   +- resolve routing per URL          -> provisional destination + category
   |
   +- interactive? -- not a TTY -----> no -+
   |                 --no-preview   -----> no -+   (skip picker, use routed default)
   |                 -d given       -----> no -+
   |                +- yes                     |
   |                   |                       |
   |       +-----------v------------+          |
   |       | PickerScreen(movie.mkv)|   enter  |
   |       | PickerScreen(ubuntu.iso)|  enter  |
   |       +-----------+------------+          |
   |                   |  esc at any point ->  |  fall back to routed default
   +-------------------+-----------------------+
   +- cmd_add(urls, chosen)             queue with the chosen dirs, print lines
   +- PreviewApp attaches               (existing behaviour)
```

### Decisions

**The picker always appears when interactive**, as the preview's first phase.
One Textual session covers picking and watching, so there is no flicker between
two full-screen programs and no second command to learn.

**One picker per file, in sequence.** Each file carries its own routed default,
so a `.mkv` and an `.iso` in the same batch each land correctly. Three files is
three `Enter` presses, and you can redirect only the one that needs it.

**Ordering inverts.** Today `cmd_add` queues immediately and the preview
attaches afterwards. The picker must run before anything is queued, so queuing
now happens after the last choice is made. The queued confirmation lines print
at that point, unchanged in content.

**`-d` skips the picker.** You already named a directory; asking again is noise.

**`--no-preview` skips the picker.** It means "don't be interactive", and a
picker that blocks while claiming to fire-and-forget would contradict itself.

**Not a TTY skips the picker**, exactly as the preview does.

**`Esc` means "use the routed default"**, not "cancel". Cancelling is `Ctrl-C`,
which aborts before anything is queued. Esc is the fast escape from a filter you
started typing and no longer want.

### Contract change

```python
cmd_add(urls, cfg, client, explicit_dir, chosen: list[Path | None] | None = None)
    -> tuple[int, list[str]]
```

`chosen` is positional, parallel to `urls`, with `None` meaning "use routing for
this one". A list rather than a URL-keyed dict so that the same URL given twice
gets two independent choices. When `chosen` is `None` — scripts,
`--no-preview`, non-TTY — behaviour is byte-for-byte what it is today.

### Pinned destinations: an existing bug this must fix

The completion hook re-resolves routing against the real filename and relocates
the file if the category changed. It passes no explicit directory, so **it
currently moves files out of a `-d` destination**, verified against the shipped
code:

```
user picked: /tmp/…/my-custom-folder/movie.mkv
after hook:  /tmp/…/Movies/movie.mkv          <- choice discarded
```

A picker that gets silently undone on completion would be worthless, so this is
in scope.

**Fix, stateless:** relocate only when the file is sitting exactly where
URL-based routing would have put it. At add time routing picks directory `A`
from the URL (unless pinned). At completion it computes `C` from the real
filename. The move `A → C` exists solely to correct a filename learned late.

```python
routed = routing.resolve(url, filename_from_url(url), cfg).path
if path.parent != routed:
    return path          # pinned by -d or the picker; leave it alone
```

If the file is anywhere other than `A`, the destination was chosen deliberately
and is left untouched. This needs no per-gid state file and fixes `-d` at the
same time. A picked directory that coincidentally equals `A` is treated as
auto-routed, which is harmless: it moves to the folder matching its own type.

---

## 2. The picker screen

**The glyphs below are literal — build exactly these**, subject to the usual
two-cell and `ascii_icons` degradation rules:

```
╭──────────────────────────────────────────────────────────────────────╮
│  Save  movie.mkv                                        file 1 of 3  │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ ser▌                                                           │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                      │
│ ▌🎬  ~/Movies/Downloads                          matched .mkv        │
│  🕘  ~/Movies/Series                             used 12×            │
│  🕘  ~/Downloads/Series-HD                       used 2×             │
│  💿  ~/Downloads/ISO                             category            │
│  📁  .                                           current dir         │
│  ✏️   ~/ser                                       create             │
│                                                                      │
│  ⏎ accept    ↑↓ choose    esc use default    ^C cancel all           │
╰──────────────────────────────────────────────────────────────────────╯
```

The icon column shows the category emoji for default and category rows, `🕘` for
recents, `📁` for the current directory, and `✏️` for the create row. The selected
row carries the `▌` accent bar used by `DownloadTable`.

**One input, two jobs.** Typing filters the list by fuzzy match. When the text
starts with `/`, `~`, or `.`, a `create` row appears at the bottom offering
exactly what was typed. There is no mode switch and no prefix key to remember.

### Candidate order

Deduplicated by resolved path, in this order:

| Rank | Source | Annotation |
|---|---|---|
| 1 | routed default for this file | `matched .mkv`, or `default folder` when uncategorised |
| 2… | recent destinations from `history.jsonl` | `used 12x` |
| … | the other category folders | `category` |
| … | current working directory | `current dir` |
| last | free text, only while the input looks like a path | `create` |

**Recents come from data already being generated.** Every history record stores
its `path`; the parent directories, counted and tie-broken by most recent use,
form the recent list, capped at 5. No new state file and no config — the picker
gets more useful the more `dl` is used.

### Keys

| Key | Action |
|---|---|
| `up` / `down` | move selection |
| `enter` | accept the highlighted row |
| `esc` | take the routed default immediately |
| `tab` | complete the highlighted path into the input for editing |
| `Ctrl-C` | cancel the whole command; nothing is queued |

### Validation

Checked on `Enter`, before queuing. A destination that cannot be created or
written shows inline and the picker stays open:

```
  cannot write to /System/Downloads
```

This is stricter than today, where an unwritable `-d` is only reported after the
queued line has already printed.

### Layout

The list caps at 8 visible rows and scrolls. Below 50 columns the annotation
column drops. Paths never truncate from the left — the tail is the informative
part.

---

## 3. Components

| File | Responsibility |
|---|---|
| `dl/destinations.py` | **new** — pure candidate building, ranking, filtering |
| `dl/tui/picker.py` | **new** — `PickerScreen` for one file |
| `dl/tui/preview.py` | `PreviewApp` gains an optional picking phase |
| `dl/cli.py` | `cmd_add` accepts `chosen` |
| `dl/hook.py` | `relocate` leaves pinned destinations alone |
| `dl/__main__.py` | builds the pending list, supplies the queue callback |

### `destinations.py`

```python
Candidate(path: Path, icon: str, note: str, kind: str)   # kind: default|recent|category|cwd|create
recent_destinations(records: list[dict], limit: int = 5) -> list[tuple[Path, int]]
candidates(default_dir, category, cfg, records, cwd) -> list[Candidate]
filter_candidates(text: str, items: list[Candidate]) -> list[Candidate]
```

No Textual, no I/O, no clock. Every ranking rule in §2 lives here, so it is
table-testable without a terminal — and it is the part most likely to be tuned
later.

### `PickerScreen(ModalScreen[Path | None])`

Renders one file's choice and dismisses with the chosen directory, or `None`
meaning "use the routed default". It reads candidates from `destinations` and
owns only widget wiring.

### `PreviewApp` picking phase

```python
PreviewApp(cfg, client, gids=(), pending=(), queue=None)
```

With `pending` non-empty the app starts in picking mode, pushes one
`PickerScreen` per file in sequence, and when the last resolves calls
`queue(chosen) -> gids`, populates `watch`, and switches to watching.

`queue` is a closure supplied by `__main__`, so the TUI layer never imports
`cli` and the one-way dependency direction from the original design holds.

### The trap this creates

`_after_refresh` exits the app when no watched item remains. During picking
`watch` is empty, so without a guard the app would exit immediately — before a
picker was ever seen. A `picking` flag makes `_after_refresh` return early until
queuing completes. This is the same shape as the disconnect-is-not-completion
trap and gets the same treatment: a dedicated test asserting the app survives a
full refresh cycle while a picker is open.

---

## 4. Failure handling

| Case | Behaviour |
|---|---|
| Chosen path not writable | Inline warning, picker stays open, nothing queued |
| Chosen path does not exist | Created on accept; a failure to create is the case above |
| `Ctrl-C` during picking | Nothing queued, exit 130, no daemon side effects |
| `Esc` | Routed default, advance to the next file |
| Empty or unreadable history | Recents absent; default and categories still offered |
| History record with an empty `path` | Skipped when building recents |
| Not a TTY, `-d`, or `--no-preview` | No picker; today's routing, unchanged |
| Daemon dies during picking | Inherited banner; picking continues; queuing fails with the existing clean RPC error |

---

## 5. Testing

**Pure unit — `destinations`:** recents ranked by count then recency;
deduplicated against the routed default; capped at 5; empty history; records
with no `path`; candidate order default → recents → categories → cwd;
annotations correct; the `create` row appears only for text starting `/`, `~`,
or `.`; fuzzy filter matches, ranks, and returns empty when nothing matches.

**Pilot — `PickerScreen`:** `Enter` accepts the preselected default; `down` then
`Enter` picks the second candidate; `Esc` dismisses with `None`; typing filters
the list; typing `~/new` offers a create row; accepting an unwritable path shows
the warning and does not dismiss; `Tab` completes into the input.

**Pilot — `PreviewApp` with pending:** one picker per file, in order; `queue`
called with the accumulated choices; `watch` populated afterwards; **the app does
not exit while picking**, even across refresh cycles; `Ctrl-C` mid-picking
queues nothing.

**Dispatch:** `-d` skips the picker; `--no-preview` skips it; non-TTY skips it;
a single URL still works; `chosen=None` leaves `cmd_add` behaviour identical.

**Pure unit — `relocate` pinning:** a file sitting in the URL-routed directory
still relocates when the real filename changes its category; a file in any other
directory is left untouched; `-d` destinations survive completion (a regression
test for the bug proven above); a missing file is still a no-op.

**Integration:** a real 5MB download picked into a non-default directory lands
there and its history row points at it — proving the chosen path survives
routing, the hook's relocate step, and history.

---

## Open decisions deferred to implementation

None.
