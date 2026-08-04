# `dl` — Download Manager

A terminal download manager with the ergonomics of IDM or Folx: a queue you can
pause and reorder, per-filetype destination folders, speed limits, and a live
animated dashboard.

`aria2c` does all the work — segmentation, resume, throttling, torrents — while
`dl` is a stateless JSON-RPC client that owns only routing policy and
presentation. Nothing runs when you are not downloading.

## Install

```bash
brew install aria2
cd downloader && make install
```

That creates a private venv at `~/.local/share/dl/venv`, installs `textual` into
it, and writes a `dl` shim to `~/.local/bin/dl`. System Python is untouched, and
arsenal's other tools stay dependency-free.

```bash
make test        # run the suite
make uninstall   # remove venv and shim
```

## Usage

```
dl <url> [url...]        queue downloads and watch them live
dl -f <file|->           queue URLs from a file or stdin
dl -d <dir> <url>        override the destination for this download
dl --no-preview <url>    queue and exit without the live preview
dl                       open the TUI

dl ls                    list downloads
dl pause <gid|all>       dl resume <gid|all>      dl rm <gid>
dl watch                 queue URLs as you copy them
dl kill                  stop the daemon
```

Before anything is queued, `dl` asks where to put each file. The routed folder is
preselected, so `⏎` accepts it; `↑` `↓` choose another, typing filters the list,
and `⇥` completes the highlighted path into the input. `Esc` takes the default,
`Ctrl-C` cancels the whole batch before anything is queued.

Candidates are the routed folder, folders you have used recently, the other
category folders, and the current directory. Recents come from your download
history, so the list improves as you use it.

Typing a path starting with `/`, `~`, or `.` switches to browsing the disk:
`~/pro` lists the real directories under `~` that start with "pro", and a
trailing slash lists everything one level down. Dotfiles stay hidden until the
fragment you type starts with a dot. If nothing on disk matches, the last row
offers to create the directory you typed.

Then `dl` attaches a live preview showing just those files. Ctrl-C detaches — the
downloads keep running — and the preview closes itself with a one-line summary
per file when they finish.

Piped or redirected output never attaches, so scripts and cron behave as before.
`--no-preview` and `-d <dir>` both skip the picker and the preview.

A destination you choose — with the picker or with `-d` — is never second-guessed
on completion. Automatic routing only corrects a file that landed in the folder
its URL implied but turned out to be a different type.

Inside the preview: `space` pause/resume, `l` limit, `o` open, `f` reveal
in Finder, `d` delete, `↑` `↓` move, `Ctrl-C` detach. Adding, reordering, and the
Completed tab are dashboard-only — run `dl` for those.

`dl ls` prints fixed columns with no colour when piped, so `dl ls | grep paused`
works.

Magnet links and `.torrent` URLs work anywhere a URL does.

## Keys

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `a` | add URL (prefilled from clipboard) | | `space` | pause/resume selected |
| `J` / `K` | reorder in queue | | | |
| `l` | speed limit for the selected download | | `r` | retry a failed download |
| `o` | open the file | | `f` | reveal in Finder |
| `p` / `u` | pause all / resume all | | `d` | delete: from list, or disk too |
| `tab` | Active ⇄ Completed | | `enter` | expand row detail |
| `↑` / `↓` | move cursor | | `q` | quit — downloads keep running |

Speed limits apply to one download at a time — there is no global throttle.

Mouse works too: click to select and scroll.

## Configuration

`~/.config/dl/config.toml`, written with commented defaults on first run.

| Key | Default | Meaning |
|---|---|---|
| `general.default_dir` | `~/Downloads` | fallback when nothing matches |
| `general.max_concurrent` | `3` | parallel downloads |
| `general.idle_timeout` | `"10m"` | daemon self-shutdown after the queue empties |
| `general.theme` | `"aurora"` | `aurora`, `ember`, `matrix`, `mono` |
| `general.ascii_icons` | `false` | replace emoji with 2-letter tags |
| `general.notify` | `true` | macOS banner on completion |
| `limits.per_download` | `"off"` | default cap per download, e.g. `"500K"` |
| `limits.connections` | `16` | connections per server |
| `limits.splits` | `16` | segments per file |
| `limits.min_split` | `"1M"` | smallest segment |

Add a category in three lines — the icon and colour flow into the TUI
automatically:

```toml
[categories.books]
dir  = "~/Books"
ext  = ["epub", "mobi", "azw3"]
icon = "📚"
hue  = "#b48ead"
```

Domain rules win over extensions. A `*.` prefix matches subdomains only, not the
apex — list both keys to match both:

```toml
[domains]
"huggingface.co" = "models"
"*.github.com"   = "code"
```

Built-in categories: video 🎬, iso 💿, archive 📦, audio 🎵, docs 📄, apps ⚙️,
models 🧠, code 💻.

## Files

| Path | Purpose |
|---|---|
| `~/.config/dl/config.toml` | your settings |
| `~/.local/state/dl/session` | queue persistence across restarts |
| `~/.local/state/dl/history.jsonl` | completed downloads, append-only |
| `~/.local/state/dl/rpc.secret` | RPC token, mode 0600 |
| `~/.local/state/dl/port` | chosen RPC port |
| `~/.local/state/dl/hooks/*.sh` | generated aria2 completion hooks |
| `~/.local/state/dl/aria2.log` | aria2 errors |

RPC binds `127.0.0.1` only and always requires the secret, so no other local
process can queue downloads to arbitrary paths.

## Troubleshooting

**`aria2c not found`** — `brew install aria2`.

**Daemon seems stuck** — `dl kill`, then any `dl` command restarts it. The queue
survives in `session`.

**`aria2c did not answer within 5s`** — the printed log tail says why. If ports
6810–6819 are all taken by other software, free one.

**Downloads vanished after a crash** — check for `session.bad`. A corrupt
session file is quarantined and the daemon restarts empty; the old file is kept
for inspection.

**Broken config** — `dl` prints the TOML error with its line number and runs on
defaults rather than refusing to start. A typo never blocks a download.

**Emoji look wrong or columns misalign** — set `ascii_icons = true`, or use
`theme = "mono"`.

## Manual checklist

The suite covers behaviour; these are the things only an eye can check.

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
