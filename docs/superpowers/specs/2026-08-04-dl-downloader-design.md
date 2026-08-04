# `dl` — Download Manager Design

**Date:** 2026-08-04
**Status:** Approved, ready for implementation planning

## Summary

A terminal download manager with the ergonomics of IDM or Folx: a queue you can
pause and reorder, per-filetype destination folders, speed limits, and a live
dashboard — but built as a thin, richly-drawn client over `aria2c`, so it costs
nothing when idle and downloads as fast as anything available.

`aria2c` owns every hard problem: multi-connection segmentation, resume,
throttling, BitTorrent. `dl` owns policy and presentation, and nothing else.

## Goals

- Add downloads without a UI taking over the terminal
- A dashboard that is genuinely pleasant to look at and to operate
- Ctrl-C never interrupts a transfer
- Per-filetype default folders, configured in three lines of TOML
- Global and per-download speed limits
- Near-zero resource cost when not downloading
- Minimal, useful feature set — no scheduling engines, no plugin systems

## Non-goals

- Per-domain authentication (cookies, headers, credentials) — explicitly cut
- Download scheduling by time of day
- Browser extension integration
- Checksum verification
- `yt-dlp` / site-specific extractors
- Mirror list management beyond what aria2 does natively

## Feature set

**Core.** Add by URL; queue with a concurrency cap; pause, resume, and cancel
per item; live progress, speed, and ETA; per-filetype destination folders;
global and per-download speed limits; automatic resume after a crash; Ctrl-C
safety.

**Agreed extras.** Batch add (multiple URLs, from a file, or from stdin) and an
opt-in clipboard watcher; BitTorrent and magnet links; a macOS notification on
completion plus a browsable history of finished downloads.

---

## 1. Architecture

Three processes. Only one is ever resident.

```
  dl <url>          dl                dl watch
  (CLI, exits)      (Textual TUI)     (opt-in poller)
        |                 |                 |
        +--------- JSON-RPC over -----------+
                 127.0.0.1:6810
                        |
                 +------v------+
                 |   aria2c    |  <- the only long-lived process
                 |  --enable-  |     queue - scheduler - transfers
                 |     rpc     |     resume - torrents - throttle
                 +------+------+
                        | --on-download-complete
                 +------v------+
                 |  dl-hook    |  <- runs ~50ms, then dies
                 +-------------+     append history - notify
```

Verified present in the installed `aria2c 1.37.0`: `--enable-rpc`,
`--rpc-listen-port`, `--rpc-secret`, `--save-session`, `--save-session-interval`,
`--on-download-complete`, `--on-download-error`, `--force-save`,
`--max-overall-download-limit`, `--max-download-limit`, `-j/-s/-x/-k/-d/-c/-i`,
`--auto-file-renaming`, `--allow-overwrite`.

`--stop-with-process` exists but is deliberately **not** used: the daemon must
outlive the process that spawned it.

### Files

| Path | Owner | Purpose |
|---|---|---|
| `~/.config/dl/config.toml` | user | routing rules, limits, defaults, theme |
| `~/.local/state/dl/session` | aria2c | queue persistence (`--save-session`) |
| `~/.local/state/dl/history.jsonl` | hook | completed downloads, append-only |
| `~/.local/state/dl/rpc.secret` | `dl` | 0600, generated on first run |
| `~/.local/state/dl/port` | `dl` | chosen RPC port when 6810 is taken |
| `~/.local/state/dl/generation` | `dl` | idle-shutdown arbitration counter |
| `~/.local/state/dl/hooks/*.sh` | `dl` | generated aria2 hook shims, 0755 |
| `~/.local/state/dl/aria2.log` | aria2c | errors only |

### Daemon lifecycle

Every invocation calls `daemon.ensure_running()`: attempt `aria2.getVersion` on
the recorded port. On connection refused, spawn `aria2c` detached
(`start_new_session=True`), restoring `--input-file=session` when that file
exists, then poll until RPC answers or a 5s timeout elapses.

Shutdown is arbitrated by a counter rather than a supervisor. See §4.

**Why Ctrl-C is safe:** the TUI is a pure RPC client. It holds no transfer state
and owns no child process. `SIGINT` tears down the Textual app and returns the
shell; `aria2c` never sees the signal because it was started in its own session
and process group.

### Install

A `dl` shim in `~/.local/bin` execs a private virtualenv at
`~/.local/share/dl/venv/bin/python`. `make install` creates the venv and
installs `textual` into it. Nothing touches system Python, and arsenal's other
tools remain dependency-free.

---

## 2. Components

Package at `dl/`. **`textual` is the only runtime dependency** — RPC is stdlib
`urllib.request`, config is stdlib `tomllib`.

```
dl/
|-- __main__.py     dispatch: URL args -> cli.add, no args -> tui, else subcommand
|-- rpc.py          Aria2 JSON-RPC client
|-- daemon.py       ensure_running() / spawn / idle-shutdown arbitration
|-- config.py       load + validate config.toml, typed defaults
|-- routing.py      (url, filename, config) -> (Path, Category)
|-- history.py      append(record) / tail(n) over JSONL
|-- cli.py          non-TUI subcommands, human-readable one-line output
|-- hook.py         aria2c --on-download-complete/-error entry point
+-- tui/
    |-- app.py      Textual App: layout, keymap, refresh loop
    |-- table.py    DownloadTable - one card per transfer
    |-- status.py   StatusBar - aggregate speed, sparkline, counts, limit
    +-- modals.py   AddUrl, SpeedLimit, Confirm
```

### Contracts

**`rpc.Aria2`** — one method per RPC call used: `add_uri`, `tell_active`,
`tell_waiting`, `tell_stopped`, `tell_status`, `pause`, `unpause`, `remove`,
`change_position`, `change_option`, `change_global_option`, `get_global_stat`,
`shutdown`. Host, port, and secret come from the constructor. Raises
`Aria2Error` on RPC faults and `Aria2Unreachable` on connection failure. Knows
nothing about config, routing, or UI.

**`routing.resolve(url, filename, config) -> (Path, Category)`** — a pure
function. No I/O, no globals.

**`daemon.ensure_running(config) -> Aria2`** — the only unit permitted to spawn
processes. Returns a connected client or raises. Callers never reason about
whether `aria2c` exists.

**`config.load() -> Config`** — a frozen dataclass. Every downstream unit
receives `Config` as a parameter rather than reading files. No module-level
state anywhere in the package.

**`tui/*`** — depends on `rpc` and `config`; depended on by nothing. The CLI
works with the `tui/` directory deleted.

**`hook.py`** — invoked by aria2c as a subprocess with `$1=gid $2=numfiles
$3=path`. Depends on `history`, `config`, and `rpc`. Not importable by the TUI.

Dependency direction is one-way: `tui` -> `rpc`/`config`; `cli` -> `daemon` ->
`rpc`; `routing`, `history`, and `config` are leaves. No cycles, and the leaves
are pure enough to test without any process running.

---

## 3. UX

Emoji are double-width and render inconsistently across fonts, so they are
confined to **fixed 2-cell reserved columns** and never appear inline in text.
Everything else uses Unicode block and braille glyphs, which occupy exactly one
cell in every terminal. This is what keeps a richly decorated table aligned.

### Launch and empty state

A splash renders for roughly 700ms on cold start, then lifts into the header.
**The glyphs below are literal — build exactly these, not ASCII approximations:**

```
                    ██████╗ ██╗
                    ██╔══██╗██║        d o w n l o a d e r
                    ██║  ██║██║        ─────────────────────
                    ██████╔╝███████╗   ⚡ powered by aria2
                    ╚═════╝ ╚══════╝
                         ▼ ▼ ▼
```

The same art is the empty state, shown with a pulsing
`press a to add a download` and lifetime stats read from history.

### Header

```
╭──────────────────────────────────────────────────────────────────────────────────────────╮
│ 🚀 12.4 MB/s   ▁▁▂▃▅▇█▇▅▃▂▄▆█▇▅▃▂▁▂▃▅▇█▇▆▄▂▁▃▅▇   ↓3  ⏳2  ✅47   🚦 off   ⏱ 04:21   │
╰──────────────────────────────────────────────────────────────────────────────────────────╯
```

The graph is a 40-sample ring buffer of total throughput, redrawn every 500ms
and colour-graded by magnitude — dim blue at the low end, rising through cyan
and green to gold at peaks. It is the most visually alive element on screen and
costs one array of 40 integers.

### Rows — two-line cards

```
  💿  ubuntu-24.04.iso                                            4.0 / 5.7 GB
      ███████████████████████▓▒░  71%    🚀 8.1 MB/s   ▂▃▅▇█▇▅▃   ⏱ 3m 21s

▌ 🎬  Severance.S02E07.mkv                                        445 / 1.4 GB
▌     ██████████▓▒░░░░░░░░░░░░░░  31%    🚀 4.3 MB/s   ▅▇█▅▃▂▁▂   ⏱ 5m 02s
▌     📂 ~/Movies/Shows · 16 conns · mirror 2/3

  📦  dataset.tar.gz                                              2.1 / 12 GB
      ██████▒░░░░░░░░░░░░░░░░░░░  18%    ⏸  paused      ▁▁▁▁▁▁▁▁   —

  🧠  big-model.safetensors                                       0 / 14 GB
      ░░░░░░░░░░░░░░░░░░░░░░░░░░   0%    ⠹  queued #2   ▁▁▁▁▁▁▁▁   —

  🗜  leaked-link.zip                                             — / —
      ░░░░░░░░░░░░░░░░░░░░░░░░░░   0%    ❌ HTTP 403    press r to retry
```

Four elements do the work:

**Comet-tail bars.** The bar is a `█` body followed by a `▓▒░` fading edge, so
the eye perceives motion between refreshes. Each bar is colour-gradiented
left-to-right across a per-filetype hue ramp.

**Filetype identity.** The emoji and the bar hue derive from the same routing
rule: video is magenta 🎬, ISO blue 💿, archive amber 📦, audio pink 🎵, docs
cyan 📄, apps green ⚙️, models violet 🧠, code orange 💻. The shape of the queue
is legible from colour alone.

**Per-row sparklines.** Each download keeps an 8-sample speed history rendered
as `▁▂▃▄▅▆▇█`. A stalling transfer visibly flattens before its ETA moves,
surfacing trouble early. Both the header and per-row buffers live in TUI process
memory only — they are never persisted, and they start empty on each launch.

**Braille spinners** (`⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏`) for connecting, queued, and metadata
states, cycling at 10fps independently of the 500ms data refresh. The selected
row gains an accent bar in its filetype hue and expands to a third line showing
the full path, connection count, and mirror status.

### Completion

A finished row flashes its bar to solid gold, gains a check mark, and slides to
the Completed tab over roughly 400ms while the macOS banner fires. The Completed
tab lists name, size, elapsed time, average speed, category, and age.

### Interaction

Mouse support comes free with Textual: click to select, scroll wheel, drag rows
to reorder the queue, click the limit indicator to open its dialog, hover to
highlight. Mouse input is always additive — every action has a key binding.

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `a` | add URL (modal, prefilled from clipboard) | | `space` | pause/resume selected |
| `d` | delete (confirm if incomplete) | | `J`/`K` | reorder in queue |
| `l` | speed limit, global | | `L` | speed limit, selected |
| `o` | reveal in Finder | | `p` / `u` | pause all / resume all |
| `tab` | Active <-> Completed | | `/` | filter by name |
| `enter` | expand row | | `r` | retry failed |
| `q` | quit (downloads keep running) | | | |

Modals are centred with rounded borders over a dimmed backdrop. The add-URL
modal offers clipboard contents as a prefilled suggestion, live-previews the
resolved destination as you type, and accepts multiple lines.

### Themes

`theme` in config selects among **aurora** (teal to violet, default), **ember**
(amber to red), **matrix** (monochrome green), and **mono** (no colour, ASCII
glyphs, for `NO_COLOR`, SSH, and logging). A theme is a dictionary of about ten
hex values plus a glyph set — swapping one is data, not code.

### Responsive behaviour

Below 80 columns the folder column drops, then ETA, then the bar shrinks to
eight cells. Below 50 columns the layout stacks. It never scrolls horizontally.

`NO_COLOR` or a dumb `TERM` forces the mono theme. `ascii_icons = true`, or a
detected non-emoji font, replaces emoji with `[ISO]`/`[VID]` tags in the same
two-cell column so alignment holds.

### Cost

The animations are string generation at 2fps over at most thirty visible rows;
Textual diffs and repaints only changed cells. Ring buffers hold 40 and 8
integers. All real work — sockets, segmentation, disk — happens in `aria2c`. An
idle TUI stays well under 1% CPU, and closing it drops even that.

### CLI surface

```
dl <url> [url...]        queue, print one line each, exit
dl -f urls.txt           batch from file (accepts `-` for stdin)
dl -d ~/somewhere <url>  override routing for this one
dl                       open TUI

dl ls                    one line per download, greppable
dl pause <id|all>        dl resume <id|all>        dl rm <id>
dl limit 2M | off        global rate limit
dl watch                 clipboard poller, Ctrl-C stops it
dl kill                  stop daemon now, keep session for resume
```

`dl ls` emits fixed columns and no colour when stdout is not a TTY, so
`dl ls | grep paused` works.

---

## 4. Config, routing, data flow

### `~/.config/dl/config.toml`

Written on first run with these defaults, fully commented:

```toml
[general]
default_dir     = "~/Downloads"     # fallback when nothing matches
max_concurrent  = 3                 # aria2 -j
idle_timeout    = "10m"             # daemon self-shutdown after empty queue
theme           = "aurora"
ascii_icons     = false
notify          = true

[limits]
global       = "off"    # or "2M" -> --max-overall-download-limit
per_download = "off"    #            --max-download-limit
connections  = 16       # -x, per server
splits       = 16       # -s
min_split    = "1M"     # -k

[categories.video]
dir  = "~/Movies/Downloads"
ext  = ["mkv","mp4","avi","mov","webm","m4v"]
icon = "🎬"
hue  = "#c678dd"

[categories.iso]
dir  = "~/Downloads/ISO"
ext  = ["iso","img","dmg","vhd"]
icon = "💿"
hue  = "#4aa3ff"

# ... archive 📦, audio 🎵, docs 📄, apps ⚙️, models 🧠, code 💻 - same shape

[domains]              # optional, wins over extension match
"huggingface.co"  = "models"
"*.github.com"    = "code"
```

Adding a category is three lines of TOML. No code change: the hue and icon flow
into the TUI because rows read their identity from the same resolved category.

### Routing

`resolve(url, filename, config)` applies four rules, first match wins:

1. An explicit `-d` on the command line, with category `other`.
2. A domain rule matching the URL host. Exact keys are tried first; a `*.`
   prefix matches **subdomains only, not the apex** — `*.github.com` matches
   `api.github.com` but not `github.com`. To match both, list both keys.
   Matching is case-insensitive and ignores any port.
3. The lowercased extension of the filename, taken from the URL path or from
   the `Content-Disposition` aria2 reports.
4. Fallback to `general.default_dir` with category `other`.

Directories are created lazily at add time, not at config load. A category
pointing at a nonexistent path is not an error until something routes there.

**Filenames are often unknown at add time** — aria2 learns them from response
headers. Routing therefore runs twice: a provisional pass over the URL path
supplies the confirmation line and the initial `dir`, and the completion hook
re-resolves against the real filename, moving the file if the category changed.
Moves are same-volume renames, so they are instant.

### Flow: adding

```
dl https://.../ubuntu.iso
   |
   +- config.load()
   +- daemon.ensure_running()          spawn aria2c if RPC refuses
   +- routing.resolve()                -> ~/Downloads/ISO, category=iso
   +- rpc.add_uri([url], {dir, max-connection-per-server, split, ...})
          -> gid "2089b05ecca3d829"
      print:  queued  ubuntu.iso  ->  ~/Downloads/ISO
```

One process spawn plus one local HTTP round-trip. With the daemon already up,
a few milliseconds.

### Flow: completion

aria2's `--on-download-complete=COMMAND` takes a **command path only** — it does
not split arguments out of the option value, and it appends its own three
(`gid`, `numFiles`, `filePath`). So `daemon.ensure_running` generates two tiny
shims at `state/hooks/complete.sh` and `state/hooks/error.sh`, each mode `0755`:

```sh
#!/bin/sh
exec ~/.local/share/dl/venv/bin/python -m dl.hook complete "$@"
```

They are rewritten on every daemon start, so a moved venv self-heals. aria2c is
then launched with `--on-download-complete=<state>/hooks/complete.sh` and the
matching `--on-download-error`.

The hook body:

```
hook.py
 +- rpc.tell_status(gid)              size, elapsed, avg speed, uris
 +- routing.resolve(real filename)    move if the category changed
 +- history.append({...})             one JSONL line, fsync'd
 +- osascript notification            when general.notify
 +- if tell_active and tell_waiting are both empty:
        bump generation counter, arm idle shutdown
```

The hook pays a cold Python start of roughly 40ms, once per completed file.
aria2 runs hooks detached, so it never blocks a transfer.

### Idle-shutdown arbitration

`state/generation` holds an integer, bumped on every add and every completion.
The hook records `(generation, now)` and spawns a detached sleeper for
`idle_timeout`. On waking, the sleeper re-reads the file and calls
`aria2.shutdown` **only if the generation is unchanged and the queue is still
empty**. Any new download bumps the counter and silently invalidates every
pending sleeper. No locks and no supervisor — the counter is the sole arbiter.

### `history.jsonl`

One object per line, append-only, never rewritten:

```json
{"ts":1754300000,"name":"ubuntu-24.04.iso","bytes":6127219712,"seconds":683,
 "avg_bps":8970000,"path":"~/Downloads/ISO/ubuntu-24.04.iso",
 "category":"iso","url":"https://...","status":"ok"}
```

The Completed tab seeks from the end and reads only the last N lines, so a
year of history opens instantly. Failures land here too, with `"status":"error"`
and the aria2 message — that record is what makes `r` retry work.

### Flow: `dl watch`

Foreground and opt-in, printing what it captures. Polls `pbpaste` every 800ms,
hashes the contents, and on change tests whether the value is a URL with a
downloadable scheme. Matches go through the same add path. A ring of the last
twenty hashes prevents re-queuing something copied twice. Ctrl-C stops it and
nothing persists.

---

## 5. Errors, security, testing

### Failure modes

| Failure | Response |
|---|---|
| `aria2c` not on PATH | Exit 1 immediately: `aria2c not found - brew install aria2`. Checked before any spawn attempt. |
| Port occupied by a foreign aria2c | Detected via an auth fault from `getVersion`. Scan 6810-6819, record the choice in `state/port`. Never kill another daemon. |
| Spawn fails or never answers | 5s timeout, print the last 20 lines of `aria2.log`, exit 1. No silent hang. |
| RPC dies while the TUI is open | Header turns red with a reconnecting indicator. Retries at 1s intervals with the last known rows frozen and dimmed, reattaching transparently. The TUI never crashes out. |
| Destination not writable | Caught at add time by an explicit `os.access` check, not by aria2 failing later. |
| Disk fills mid-download | aria2 errors that download; the hook records it; other downloads continue. |
| HTTP 403/404/timeout | aria2 retries per `--max-tries=5 --retry-wait=3`. After exhaustion the row shows the real status and `r` re-adds with the same options. |
| Corrupt `session` file | Detected, renamed to `session.bad`, daemon restarted empty, user informed. Never an unrecoverable state. |
| Malformed `config.toml` | Print the `tomllib` error with its line number, then run on defaults. A typo in a hue must never block a download. |
| Non-TTY stdout | Refuse to open the TUI; suggest `dl ls`. |
| Terminal under 50 columns | Stacked layout, then a plain message if even that will not fit. |
| `textual` missing from the venv | The shim detects the import failure and prints the `make install` line. CLI subcommands still work — they do not import `textual`. |

The governing rule: a failure of one download never touches another, and a
failure of the UI never touches a transfer.

### Security

RPC binds `127.0.0.1` only (`--rpc-listen-all=false`, the default). A 32-byte
`secrets.token_urlsafe` token is generated on first run into `state/rpc.secret`
at mode `0600` and passed via `--rpc-secret`; every client call sends
`token:<secret>`. Without this, any local process could queue downloads to
arbitrary paths through an open RPC port.

The `dir` sent to aria2 is always a resolved absolute path.
`--auto-file-renaming=true` with `--allow-overwrite=false` prevents a hostile
`Content-Disposition` from overwriting an existing file or escaping the
destination.

### Testing

`pytest` as a dev dependency in the same venv, run by `make test`.

**Unit — pure, fast, no processes.**
`routing.resolve` gets a table of roughly forty cases: extension hits, domain
overrides, `*.` wildcards, URLs without extensions, query strings after the
filename, uppercase extensions, and `-d` precedence. `config` is tested for
applied defaults, merged partial files, ignored unknown keys, and fallback on
malformed TOML. `history` covers append-then-tail, tail on an empty file, and
tail on a file whose final line was truncated by a crash. Formatters are tested
across bytes, durations, sparklines, and bar rendering at every width from 4 to
40 cells and every percentage from 0 to 100.

**`rpc.Aria2` against a fake server.** A stdlib `http.server` on an ephemeral
port replays recorded aria2 JSON, covering every method plus fault responses,
auth rejection, and connection-refused. No real `aria2c` required.

**Integration — real aria2c, no internet.** A local `http.server` serves a 5MB
temporary file. The suite spawns a real daemon on an ephemeral port and drives
the full cycle: add, route, download, hook fires, file lands in the correct
category directory, history line appended, idle shutdown arms and fires. It
also covers pause and resume mid-transfer and deletion with a partial file on
disk. The suite runs in a few seconds and is fully hermetic — no test touches
the network.

**TUI via Textual's `run_test()` pilot.** Keypresses are driven against a
stubbed RPC client and assertions run over rendered output: `space` toggles
pause, `J`/`K` issue `change_position`, `tab` switches tabs, narrow widths drop
the correct columns, and `NO_COLOR` produces zero escape sequences.

**Deliberately not automated:** animation smoothness, gradient aesthetics,
emoji rendering in a specific font, and macOS notification banners. These live
in a manual checklist in the README, because automating them would cost more
than it catches.

---

## Open decisions deferred to implementation

None. Every question raised during design was resolved above.
