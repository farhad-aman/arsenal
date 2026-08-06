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
dl -p <url>              download through the sing-box proxy
dl -H "Key: Value"       extra request header (repeatable)
dl --no-preview <url>    queue and exit without the live preview
dl                       open the TUI

dl ls                    list downloads
dl history [n]           list finished downloads (--failed, --json)
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

`dl ls` shows the live queue and anything that failed. Finished downloads are
in `dl history`, which is a file on disk and outlives the daemon; a removed one
is gone from both. The two never show the same download.

Both take a name to match: `dl ls ubuntu`, `dl history ubuntu`, `dl history 50
ubuntu`. Matching is the same as `/` in the dashboard — case-insensitive and
Unicode-normalised — and a history query reads the whole log rather than the
last twenty, so an old download is findable.

## One daemon

There is one aria2 daemon on one port, and one dashboard. A second `dl` window
is refused rather than opened beside the first: both would act on the same
queue from their own idea of what is in it.

`dl <url>` normally attaches a live view of what it just queued. That view is a
dashboard too, so with one already open it stands down and prints a line
instead — the download is queued either way, and the open window is where it
appears.

`dl watch` takes the lock as well. It has no window, but it runs until stopped
and queues downloads while it does, which makes it a copy of dl rather than one
of its commands.

Everything else — `dl ls`, `dl history`, `dl pause`, `dl rm` — is a short-lived
client and runs whenever, including alongside an open dashboard.

A lock left behind by something that was killed names a process that is gone,
and is taken over rather than honoured, so a crash cannot shut you out.

Earlier versions moved to the next free port when theirs was busy, which is how
a machine ends up carrying daemons nothing can reach — holding downloads that
never appear anywhere. Starting `dl` now brings its own daemon back to the one
port, carrying its queue across; `dl kill --strays` finds anything left over
from before and stops it once you have seen what it is.

If the port is genuinely held by something else, `dl` says so instead of
quietly starting somewhere new.

Nothing in the environment can move any of this. The state directory, the
config file and the port are fixed in the code, because each of them was
otherwise a way to start a second copy — and two copies acting on downloads
neither can see is the inconsistency all of this exists to prevent. The daemon
tells its own hook where to write rather than exporting it.

To run a genuinely separate copy, give it a separate machine: a container or a
VM. That is a boundary dl does not have to know about.

`dl ls` prints fixed columns with no colour when piped, so `dl ls | grep paused`
works.

`dl history` prints what has already finished, newest first, with the folder each
file landed in:

```
2026-08-05 15:10  error       0 B  video   Furious.S01E01.480p.mkv  — HTTP 410 Gone
2026-08-05 12:23  ok         17 MB  video   کلیپ.mp4  →  ~/Downloads/Movies  🌐
2026-08-05 06:50  ok        5.7 GB  iso     ubuntu-24.04.iso  →  ~/Downloads/ISO
```

`--failed` keeps only what broke, `--json` emits the raw records for `jq`, and a
bare number caps how many. Only the leading columns are padded — padding a name
of double-width or right-to-left characters would land the rest of the line
somewhere different on every row. It reads a file, so it never starts the daemon.

`dl watch` catches URLs as you copy them, through the same routing, proxy and
duplicate rules as everything else. Nothing there can prompt, so a duplicate is
left alone with a note rather than guessed at, and a YouTube link is taken at
best quality — run `dl <url>` when you want the choice.

Magnet links and `.torrent` URLs work anywhere a URL does.

## Duplicates

Once the destination is settled, `dl` checks whether the download would collide
with something and asks before writing anything. What it offers depends on what
actually matches:

| Match | What it means | Options |
|---|---|---|
| URL **and** path | The same file, headed for the same place | skip · rename · overwrite |
| URL only | Same file, different folder — nothing is at risk | skip · download anyway |
| Path only | A **different** URL produced the file sitting there | skip · rename · overwrite ⚠ |

Overwriting a path-only match carries a blunt warning, because the file being
destroyed was never produced by this download.

If what you are duplicating is still downloading, overwrite drops that download
from the queue first — the old entry disappears, its partial file and `.aria2`
go with it, and the new one starts clean.

Rename is the old `.1` behaviour, now chosen rather than silent. It turns
`--continue` off for that download: left on, aria2 resumes *into* the existing
file instead of renaming, destroying the copy rename exists to preserve.

`Esc` cancels the whole batch. Nothing is queued and nothing on disk is touched.

Non-interactive runs — `--no-preview`, piped output, cron — cannot ask, so they
behave exactly as before: aria2 auto-renames to `file.1.mkv` and nothing is ever
overwritten without someone saying so.

## Proxy

`dl -p <url>` sends that download through the sing-box proxy. It is per-download:
everything else in the queue keeps going out directly, so you can pull one
blocked file through the proxy at full speed without tunnelling the rest.

Proxied rows are badged 🌐 in the dashboard, so a queue with both kinds in it
stays readable:

```
  🎬  Furious.S01E01.mkv 🌐                        412 MB / 1.2 GB
  💿  ubuntu-24.04.iso                             1.9 GB / 5.7 GB
```

The badge is read back from aria2 rather than remembered locally, so it is right
even in a dashboard opened long after the download was queued, from another
shell. Each download is asked about once and the answer cached.

```toml
[proxy]
url = "http://127.0.0.1:2080"
```

The address must speak HTTP — aria2 has no SOCKS support at all. sing-box's
`mixed` inbound serves HTTP and SOCKS on the same port, so the default works
against `vpn -s` as-is.

The daemon is started with `http_proxy`, `no_proxy` and friends stripped from its
environment. Without that, a shell with `vpn -p` active would proxy every
download, and because the daemon outlives that shell the setting would stick for
downloads queued later from anywhere. `-p` is the only thing that decides.

## Keys

| Key | Action | | Key | Action |
|---|---|---|---|---|
| `a` | add URL (prefilled from clipboard) | | `space` | pause/resume selected |
| `J` / `K` | reorder in queue | | | |
| `l` | speed limit (aria2 downloads only) | | `r` | retry a failed download |
| `o` | open the file | | `f` | reveal in Finder |
| `p` / `u` | pause all / resume all | | `d` | delete: from list, or disk too |
| `s` | settings | | `tab` | Active ⇄ Completed |
| `/` | search by name | | `enter` | expand row detail |
| `S` | cycle sort order | | `R` | reverse it |
| `y` | copy source URL | | `Y` | copy file path |
| `↑` / `↓` | move cursor | | `q` | quit — downloads keep running |

Speed limits apply to one download at a time — there is no global throttle, and
they reach aria2 downloads only.

YouTube downloads share the table but not aria2's queue: `space` pauses and
resumes them by stopping yt-dlp and picking the fragments back up, and `J`/`K`
do not apply because they start immediately rather than queueing.

Mouse works too: click to select and scroll.

On the Completed tab, `r` downloads a finished entry again — through the same
routing, proxy and duplicate checks a fresh download gets, not a bare re-add.
`y` copies its source URL and `Y` its path; both work on the Active tab too.

A YouTube entry cannot go back to aria2, which would fetch the watch page as
HTML, so `r` opens the quality picker and the destination picker the way `dl
<url>` does. Pasting a watch URL into `a` now does the same; a mixed batch
sends the direct URLs to aria2 first and asks about the YouTube ones after,
so two questions never stack on one another.

## Playlists and channels

A playlist or channel address expands into its videos: `dl` lists what is in
there, says how many, and waits. Nothing is queued until you agree, because a
channel can hold thousands.

Quality and destination are asked once for the whole collection, then one job
is queued per video — each with its own progress, and its own `space`, `r` and
`d`, so one failure does not take the rest down with it.

A video address copied while watching inside a playlist carries `list=`, and
still downloads only that video. Only a bare playlist or channel address means
the collection:

```
youtube.com/watch?v=abc&list=PLxyz   →  that video
youtube.com/playlist?list=PLxyz      →  the playlist
youtube.com/@channel/videos          →  the channel
```

Listing is flat — one request for the whole collection rather than one per
video — so it is quick even for a long channel, and the titles come back with
it. That is also why no size is shown: knowing it means extracting every video
in turn, which is minutes of waiting before anything downloads.

Collection videos skip the per-video duplicate check for the same reason. A
file already on disk is left alone by yt-dlp rather than asked about, so
running a playlist again to pick up what has been added since costs nothing
for the videos you already have: they finish at once, pointing at the file
that is already there.

`max_concurrent` holds these back too, so accepting a long collection starts
that many and leaves the rest queued. Each one, as it finishes, starts the next
— there is no scheduler process, and nothing waits idle. A playlist queued from
the command line keeps working through itself after you close the terminal.

`r` and `space` ignore the cap: asking for one download back is an instruction
about that download, not a request to join the queue.

## Search

`/` filters both tabs by filename. Rows that do not match are hidden, so `J`,
`K`, `space` and `d` act on the short list in front of you rather than on
something scrolled off screen.

Typing filters as you go. `enter` puts the keyboard back on the list and keeps
the filter — the bar underneath shows what is on and how much it hides. `esc`
clears it.

One query covers both tabs and follows you across `tab`. Matching is
case-insensitive, and normalises Unicode first: macOS stores filenames
decomposed while your terminal sends what you typed composed, so without it a
Persian or accented name would never match itself.

On the Completed tab the search reads the whole of `history.jsonl`, not the last
200 records the tab shows — a search that stopped at the cutoff would quietly
miss older downloads.

## Sort

`S` steps through the orders, `R` reverses the one you are in. Active sorts by
queue, name, size, speed or progress; Completed by recent, name or size — speed
and progress mean nothing for a finished download. Each tab keeps its own.

Each order starts in the direction worth seeing first: names A→Z, but sizes and
speeds largest first, so `S` alone is usually enough.

Sorting is stable, so queue order breaks ties, and it applies to whatever the
filter has left. The bar under the list names the order, alongside the filter
when both are on.

`J`/`K` reorder the aria2 queue, which only means something in queue order — in
any other they refuse and say so rather than moving a row somewhere unrelated.

aria2 reports no size for a download it has not started, so sorting a mostly
queued list by size groups those at one end.

## Settings

`s` in the dashboard opens every setting as a screen: General, Limits, YouTube and
Hooks as forms, plus list editors for proxy domains, per-host headers and
categories.

The theme previews as you move through it so you can see what you are choosing;
everything else applies on `^S`. `Esc` discards, including an unsaved theme
preview.

Nothing needs a restart. Most settings are read when they are next used, so they
apply to the next download, and `max_concurrent` is pushed to the running daemon.

Saving rewrites only the values you touched — the comments, blank lines and
column alignment of your `config.toml` all survive.

If `config.toml` has a syntax error the screen refuses to open rather than
overwrite it. `dl` runs on defaults until the file parses, and saving those
defaults over your file would destroy what you wrote.

Everything remains editable by hand; the screen is a second way in, not a
replacement. A running `dl` will not notice edits made in an editor, though —
restart it, or make the change from the screen.

## Configuration

`~/.config/dl/config.toml`, written with commented defaults on first run.

| Key | Default | Meaning |
|---|---|---|
| `general.default_dir` | `~/Downloads` | fallback when nothing matches |
| `general.max_concurrent` | `3` | parallel downloads, aria2 and YouTube alike |
| `general.idle_timeout` | `"10m"` | daemon self-shutdown after the queue empties |
| `general.theme` | `"aurora"` | `aurora`, `ember`, `matrix`, `dusk`, `mono` |
| `general.notify` | `true` | macOS banner on completion |
| `hooks.on_complete` | `""` | command to run after each finished download |
| `hooks.timeout` | `"5m"` | how long that command may take |
| `proxy.url` | `http://127.0.0.1:2080` | where `-p` and `proxy.domains` send traffic |
| `proxy.domains` | `[]` | hosts always downloaded through the proxy |
| `youtube.cookies_from` | `"chrome"` | browser to borrow YouTube cookies from |
| `youtube.probe_timeout` | `"3m"` | how long to wait for YouTube to describe a link |
| `limits.per_download` | `"off"` | default cap per download, e.g. `"500K"` |
| `limits.connections` | `16` | connections per server |
| `limits.splits` | `16` | segments per file |
| `limits.min_split` | `"1M"` | smallest segment |

Listing a host under `proxy.domains` means every download from it goes through
the proxy, so `-p` is only needed for one-offs:

```toml
[proxy]
url     = "http://127.0.0.1:2080"
domains = ["youtube.com", "googlevideo.com"]
```

A bare name covers its subdomains as well — a service you cannot reach is
unreachable at every hostname it answers on. `*.` matches subdomains only,
as it does under `[domains]`.

Because the rule travels with the URL rather than the command line, retries,
the dashboard's add box and `dl watch` all proxy the same downloads `dl -p`
would.

Before a YouTube download starts, `dl` asks yt-dlp what the link actually is —
the title, the size, and the exact filename it would write. That answer is what
the duplicate check compares against, and over a proxy it can take anywhere from
twenty seconds to a couple of minutes. If it never arrives, `dl` says so and asks
rather than queueing blind, because a download queued without it would be
silently declined by yt-dlp when the file is already there.

Hosts that check `Referer` — or want a token, or a particular user agent — get
their headers from `[headers]`:

```toml
[headers."indllserver.info"]
Referer = "https://indllserver.info/"
```

Same host rule as `proxy.domains`, so one entry covers `dl6`, `dl7` and whatever
they use next. Every matching rule contributes and the more specific one wins a
clash, so a site-wide `Referer` and a per-host token coexist.

Headers reach aria2 over RPC, never on a command line, so a `Cookie` or
`Authorization` value does not show up in `ps`. It is still sitting in a config
file, though — that file is as private as its permissions make it.

`-H "Key: Value"` adds one for a single download, alongside anything configured.

`hooks.on_complete` runs your own command once a download lands:

```toml
[hooks]
on_complete = "~/bin/dl-done.sh"
```

```sh
#!/bin/sh                       # $1 path   $2 category   $3 url
case "$2" in
  archive) unar -d "$1" && rm "$1" ;;
  video)   mv "$1" ~/Movies/ToWatch/ ;;
esac
```

It runs for YouTube downloads too, and never through a shell — a filename
containing `;` or `$(...)` arrives as text rather than as something to execute.

A hook that fails or exceeds `timeout` never fails the download: the bytes
arrived, and what you asked to happen afterwards is a separate thing that can go
wrong. Failures go to `~/.local/state/dl/hook.log` and, if notifications are on, a banner.

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
| `~/.local/state/dl/yt/*.json` | yt-dlp job records, swept on dashboard start |
| `~/.local/state/dl/yt/*.part` | yt-dlp fragments, removed with their job |
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

**YouTube downloads refuse to start** — they need ffmpeg. YouTube serves video
and audio as separate streams above 360p, and combining them is ffmpeg's job,
as are audio-only downloads and subtitles. `dl` checks before fetching
anything: without the check yt-dlp downloads the streams and fails at the last
step, leaving a `.webm` where the file you asked for should be.
`brew install ffmpeg`.

**Hard subtitles fail on macOS** — homebrew-core's `ffmpeg` is built without
libass, so it has no `subtitles` filter and `brew reinstall ffmpeg` produces the
same binary. `brew install homebrew-ffmpeg/ffmpeg/ffmpeg` builds one that can.
Soft subtitles need nothing extra.

**Emoji look wrong or columns misalign** — set `theme = "mono"`, which swaps
every emoji for an ASCII stand-in. It is the only theme without them.

## Manual checklist

The suite covers behaviour; these are the things only an eye can check.

- [ ] Splash renders correctly and clears after ~700ms
- [ ] Progress bars animate smoothly; the comet tail is visible
- [ ] Header sparkline changes colour with throughput
- [ ] Emoji render in your terminal font and columns stay aligned
- [ ] `theme = "mono"` keeps alignment with no emoji
- [ ] `/` filters as you type and the count under the list is right
- [ ] With more rows than fit, `↑`/`↓` move the selection and the view follows
- [ ] `S` reorders the list visibly and the bar underneath names the order
- [ ] `y` on a finished download puts its URL on the clipboard
- [ ] `r` on a finished YouTube entry opens the quality picker, not an aria2 add
- [ ] A playlist URL shows its real name and count before queuing anything
- [ ] `NO_COLOR=1 dl` emits no colour
- [ ] Resize below 80, 66, and 50 columns — layout degrades, never scrolls sideways
- [ ] macOS notification appears on completion
- [ ] Ctrl-C in the TUI leaves downloads running (`dl ls` confirms)
- [ ] All four themes look correct
