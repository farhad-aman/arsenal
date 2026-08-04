# `dl` Inline Preview Design

**Date:** 2026-08-04
**Status:** Approved, ready for implementation planning
**Builds on:** `2026-08-04-dl-downloader-design.md`

## Summary

`dl <url>` currently queues a download, prints one line, and exits. This adds a
live preview scoped to the downloads just queued, so the shell session that
started them can watch and control them without opening the full dashboard.

Ctrl-C detaches and leaves the downloads running, exactly as it does in the main
TUI.

## Goals

- Watch progress for the files you just queued, in the terminal you queued them from
- Pause, resume, limit, open, reveal, and delete those files without leaving that session
- Never block automation: piped or redirected invocations behave exactly as before
- Reuse the dashboard's rendering so the two views cannot drift apart

## Non-goals

- Adding new URLs from inside the preview
- Reordering the queue from the preview
- A second, inline (non-full-screen) rendering path
- Changing any behaviour of `dl` with no arguments, or of any subcommand

---

## 1. Behaviour and flow

```
dl https://.../a.iso https://.../b.mkv
   |
   +- ensure_running()                        daemon up
   +- cmd_add() -> collects gids              ["2089b0...", "7f3c11..."]
   |     prints the queued lines as today     (stays in scrollback)
   |
   +- attach? -- stdout not a TTY ---> no ---> exit 0
   |             --no-preview        ---> no ---> exit 0
   |             all adds failed     ---> no ---> exit 1
   |            +- yes
   +- PreviewApp(gids).run()                  full-screen, live
   |     Ctrl-C / q  ---> detach, downloads continue
   |     all gids settled ---> exit()
   |
   +- print summary lines                     one per gid, after exit
```

### Decisions

**Preview is the default when interactive.** It attaches whenever stdout is a
TTY. When output is piped or redirected — `dl url | tee`, cron, scripts — `dl`
prints the queued line and exits as it does today. Automation never hangs.

**`--no-preview`** covers the case where stdout is a TTY but you still want to
fire and forget, such as queuing several downloads in a row.

**All queued URLs are previewed**, stacked as cards with a cursor, not just the
first. The same widget renders one row or ten.

**Exit on completion.** When every previewed download settles, the preview
closes itself and prints a permanent one-line result per file, leaving the shell
prompt back and the results in scrollback.

**"Settled"** means the gid appears in neither `tell_active` nor `tell_waiting`
— complete, errored, *and* removed all count. Deleting the only previewed
download therefore exits the preview rather than leaving an empty screen.

**Detach needs no new machinery.** The TUI is a pure RPC client that owns no
child process; `SIGINT` tears down the Textual app and returns the shell while
aria2c continues in its own session and process group. This is the existing
guarantee, now reachable from `dl <url>`.

### Contract change

`cmd_add` currently returns an exit code and discards the gid that `add_uri`
returns. It becomes:

```python
cmd_add(urls, cfg, client, explicit_dir) -> tuple[int, list[str]]
```

Gids come back in argument order, and rejected URLs contribute no gid. Every
caller updates with it.

---

## 2. Interface

```
+------------------------------------------------------------------------------------------+
| 12.4 MB/s   <sparkline>   down 2  waiting 1  done 47   limit off   00:42                  |
+------------------------------------------------------------------------------------------+

   [ISO]  ubuntu-24.04.iso                                        4.0 / 5.7 GB
          <comet bar>  71%    8.1 MB/s   <sparkline>   3m 21s

|  [VID]  Severance.S02E07.mkv                                    445 / 1.4 GB
|         <comet bar>  31%    4.3 MB/s   <sparkline>   5m 02s

 space pause/resume   l limit   L limit this   o open   f finder   d delete   ^C detach
```

Rendering is identical to the main dashboard — same `DownloadTable`, same
`StatusBar`, same theme, same emoji and comet-tail bars. The only visible
differences are the hint line and the absence of a Completed tab.

### Keymap

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `space` | pause/resume selected | | `l` / `L` | speed limit, global / this file |
| `o` | open the file | | `f` | reveal in Finder |
| `d` | delete: from list, or from disk too | | `enter` | expand detail line |
| `up` / `down` | move cursor | | `p` / `u` | pause all / resume all |
| `Ctrl-C` or `q` | detach — downloads continue | | | |

**`p` and `u` are scoped to the watch set**, not to every download on the
machine. They iterate the displayed rows, which the preview has already filtered
— so "pause all" in a preview means "pause the files I just queued". `l` remains
genuinely global, because it maps to aria2's overall rate limit; `L` applies to
the selected file only.

**Deliberately dropped:** `a` (add), `tab` (completed), `J`/`K` (reorder), `r`
(retry). Adding a URL mid-preview raises "does the new one join the watch set?",
a question with no clean answer. Reordering is meaningless within a hand-picked
set. Retry belongs with the Completed tab's history.

### The status bar stays global

It reports total throughput across all downloads, not only the previewed ones.
When one file is slow the useful question is whether something else is
saturating the link, and a scoped meter would hide exactly that.

### Summary output

Printed after Textual releases the screen, so it persists in scrollback. **These
lines are literal — build exactly these glyphs**, unlike the abbreviated card
mockup above (whose rendering is inherited verbatim from `DownloadTable`):

On completion:

```
  ✅ ubuntu-24.04.iso       5.7 GB in 11m 23s   avg 8.6 MB/s
  ❌ Severance.S02E07.mkv   HTTP 403
```

On detach, it reports what is still running, so it is clear nothing stopped:

```
  ⏳ 2 still downloading — `dl` to watch, `dl ls` to list
```

Under `NO_COLOR` or `ascii_icons`, the marks degrade to `[ok]` / `[fail]` /
`[...]` in the same two-cell column, consistent with the theme rules in the
parent spec.

---

## 3. Components

| File | Change |
|---|---|
| `dl/tui/preview.py` | **new** — `PreviewApp`, `summarise`, `run_preview` |
| `dl/cli.py` | `cmd_add` returns `(rc, gids)` |
| `dl/__main__.py` | `--no-preview` flag, attach decision, print summary |
| `tests/test_preview.py` | **new** |

### `PreviewApp(DlApp)`

Overrides exactly three things and inherits everything else:

- `__init__(cfg, client, gids)` — stores the watch set, swaps the hint line
- `refresh_data()` — calls `super()`, filters `self.table.rows` to the watch set,
  and when no watched gid remains in `tell_active`/`tell_waiting`, records the
  final statuses and calls `self.exit()`
- `BINDINGS` — the reduced keymap above

`DlApp.refresh_data` renders the splash art when it sees an empty queue. The
preview must suppress that: a momentarily empty watch set means "about to exit",
not "nothing to do", and flashing the logo on the way out would look like a
glitch.

Subclassing is the point of this approach: bars, sparklines, the delete modal,
the disconnect banner, and theming are inherited untouched and cannot drift from
the main dashboard.

### `summarise(final, cfg) -> list[str]`

A pure function: a mapping of gid to final status in, printable lines out. No
I/O and no Textual, which keeps the part with the most formatting detail
trivially testable. `__main__` prints whatever it returns.

### `run_preview(cfg, client, gids) -> list[str]`

Constructs and runs the app, returns the summary lines for the caller to print.
The single seam that dispatch tests stub.

---

## 4. Failure handling

| Case | Behaviour |
|---|---|
| Daemon dies mid-preview | Inherited banner `daemon lost — reconnecting`; rows freeze and dim; reattaches transparently; never crashes out |
| Daemon never returns | Preview stays until Ctrl-C. It must **not** self-exit: an empty `tell_active` caused by a disconnect must never be read as "everything finished" |
| Some URLs queued, others rejected | Preview attaches for the successes; failures already on stderr; exit code stays 1 |
| Every URL rejected | No preview, exit 1 |
| Download deleted from inside the preview | Counts as settled; if it was the last, the preview exits |
| Not a TTY | Never attaches |
| Very small terminal | Degrades via the inherited responsive layout |

The disconnect-versus-finished distinction is the one real correctness trap in
this feature and gets a dedicated test.

---

## 5. Testing

No network, and no real daemon outside the integration test.

**Pure unit — `summarise`:** success with size and duration; error carrying the
aria2 message; a mixed batch; a still-running set (detach wording); an empty set.

**Pure unit — `cmd_add` contract:** gids returned in argument order; empty list
when every URL is rejected; the `(rc, gids)` tuple shape; existing callers still
behave.

**Textual pilot — `PreviewApp`:** displays only the watch set even when the
daemon reports unrelated downloads; `space` pauses the selected gid; exits once
both watched gids leave active and waiting; does **not** exit while one is still
waiting; does **not** exit when `tell_active` raises `Aria2Unreachable`; never
renders the splash art; `p` pauses only the watched gids when the daemon also
reports unrelated downloads.

**Dispatch:** `dl url` with a TTY attaches; without a TTY does not; with
`--no-preview` does not; a total add failure returns 1 without attaching.
Verified by stubbing `run_preview` and asserting whether it was called.

**Integration:** extends the existing local-server suite — queue a real 5MB
download, attach, confirm the preview exits on completion and the file landed in
its routed directory.

---

## Open decisions deferred to implementation

None.
