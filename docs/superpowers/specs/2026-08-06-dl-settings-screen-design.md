# `dl` — Interactive Settings

## Problem

Every setting lives in `~/.config/dl/config.toml`. Changing one means leaving the
app, finding the file, remembering the key's name and its accepted spelling, and
restarting. The config has grown from 12 keys to 20 in a day — proxy domains,
per-host headers, probe timeout, completion hooks — and each addition makes the
file a worse interface.

## Goal

Edit every setting from inside `dl`, and have the change take effect at once.

## Scope

In: all settings, including the list-shaped ones (proxy domains, per-host
headers, categories).

Out: a `dl settings` subcommand (`s` from the dashboard covers it), search across
settings, reset-to-defaults, import/export, and watching `config.toml` for
changes made outside the app.

## Key finding: nothing needs a daemon restart

Most values are read at the moment they are used, so a reloaded config applies to
the next download by itself: `connections`, `splits`, `min_split`,
`per_download`, proxy url and domains, headers, categories, domain routing,
`cookies_from`, `probe_timeout`, `on_complete`, `hook_timeout`, `idle_timeout`.

The one value baked into the running daemon is `max_concurrent`. aria2 accepts it
live — verified against a real daemon:

```
max-concurrent-downloads   -> accepted, now '7'
max-overall-download-limit -> accepted, now '1048576'
max-connection-per-server  -> accepted, now '8'
split                      -> accepted, now '8'
```

Only `max-concurrent-downloads` is pushed. The other three are already set
per-download in `add_options()` at queue time, and pushing them globally would
change behaviour for downloads queued by anything other than `dl`.

## Architecture

Three modules, UI kept apart from logic:

| File | Purpose | Depends on |
|---|---|---|
| `dl/settings.py` | Schema: what settings exist, their types, allowed values, help. Validation. No UI, no I/O. | `config` |
| `dl/tomlio.py` | Read/write `config.toml` through tomlkit, preserving comments. Set a value by path. | `tomlkit` |
| `dl/tui/settings.py` | Screens. Renders from the schema, edits, saves. | both |

`tomlkit` becomes the second dependency after `textual`, installed into `dl`'s
private venv. It is what keeps comments and layout intact across a save; the
standard library reads TOML but cannot write it.

### The schema entry

```python
@dataclass(frozen=True)
class Field:
    path: tuple[str, ...]     # ("general", "theme") — where it lives in the TOML
    label: str                # "Theme"
    kind: str                 # choice | int | rate | duration | path | bool | text
    choices: tuple = ()
    help: str = ""
    live: bool = False        # preview immediately rather than on save
```

`live=True` on exactly two fields: `theme` and `ascii_icons`.

Validators reuse `config.parse_rate`, `config.parse_duration` and
`destinations.ensure_writable`, so the screen accepts exactly what the config
file accepts. No second dialect.

### Screens

```
Settings
├─ General        theme · icons · notifications · default folder · idle timeout
├─ Limits         concurrent · connections · splits · min split · speed cap
├─ YouTube        cookies from · probe timeout
├─ Hooks          on_complete · timeout
├─ Proxy       ›  url + domain list
├─ Headers     ›  host / key / value rules
└─ Categories  ›  8 categories, each dir · extensions · icon · colour
```

The four unmarked sections are one screen class driven by different field lists.
Only the three marked `›` are bespoke, because their shapes genuinely differ.

Headers are nested two deep in TOML (`[headers."host"]`, then key/value). The
editor flattens them to `(host, key, value)` rows — add, edit, delete a row — and
reassembles the nesting on write. One flat list, no second level of drilling.

Proxy is a mixed screen: `url` is an ordinary schema field rendered above a
domain list with add, edit and delete.

Categories drill in, because a category is a record with four fixed fields; once
inside, it is the same schema-driven form as General. Categories can be added and
deleted as well as edited — the built-in eight have no special status, since
`config.load()` already merges user categories over the defaults.

A category's `ext` is itself a list. Rather than a third level of list editing it
is one text field holding a comma-separated string (`mkv, mp4, avi`), split on
save. Extensions are short and edited rarely; a dedicated list screen would cost
more than it returns.

## Data flow

Opening: `s` from the dashboard. It is a `ModalScreen`, so the existing
`DlApp.check_action` guard stands the dashboard's keys down while it is up.

While editing, nothing touches disk. Edits accumulate in a working copy keyed by
schema path:

```
{("limits", "connections"): 8, ("general", "theme"): "ember"}
```

### Live fields

`theme` and `ascii_icons` apply the instant they change so they can be seen, but
they remain previews:

```
change theme → assign widget.theme_data, refresh   (visible now)
             → record in working copy              (persists on ^S)

Esc          → restore theme from the original cfg (preview undone)
^S           → write to disk, reload               (preview committed)
```

Escaping out of a preview must put the colours back.

### Saving

```
validate every changed field
        ↓ any failure → stop, show it inline, write nothing
tomlio.write(path → value)         comments and layout preserved
        ↓
config.load()                      re-read, so what is applied is what is on disk
        ↓
app.reload_config(new_cfg)
```

Re-reading rather than trusting the working copy means the running app and the
file cannot disagree: the app sees what a restart would see.

### `reload_config`

1. Swap `self.cfg`.
2. Re-select the theme and push it to the status bar, table and completed table.
   Each widget stores `self.theme_data` at construction and re-reads it at render
   time, so this is three assignments and a refresh, not a rebuild.
3. Push `max_concurrent` to the live daemon via `change_global_option`, only if
   it changed.

Nothing else needs doing.

### Deliberate non-behaviour

Changing `per_download` does not retroactively throttle downloads already
running. It is the default for new ones; `l` changes a running download. Same for
`connections` and `splits`, which aria2 fixes when a download starts.

## Error handling

### A broken config file must not be overwritten

`config.load()` falls back to defaults when the TOML is invalid, warns, and
carries on — right for downloads, a trap here. With a syntax error in the file,
the app runs on defaults while the file says something else, and saving would
write those defaults over it.

The settings screen therefore parses the file itself on open, and refuses if that
fails:

```
config.toml has a syntax error on line 14 — fix it before editing here.
dl is running on defaults until it parses.
```

No editing, no save, nothing overwritten.

### The rest

| Case | Behaviour |
|---|---|
| Invalid value typed | Inline message under the field, save blocked, nothing written |
| File missing | `config.write_default()` first — already exists |
| Write fails (permissions, disk full) | Temp file + atomic replace, so a failure leaves the original intact |
| Daemon unreachable when pushing `max_concurrent` | Save still succeeds; applies at next daemon start. A settings save must not fail because aria2 is down |

## Testing

1. **Schema** — every field's validator accepts its own default; junk is rejected
   with a message naming the problem.
2. **tomlio** — round-trip preserves comments, blank lines and key order; nested
   values update; missing sections are created; a failed write leaves the
   original byte-identical.
3. **Screens**, pilot-driven like the existing `ytoptions` tests — navigation,
   edit, `Esc` discards, `^S` saves, an invalid value blocks the save, and the
   theme preview reverts on `Esc`.
4. **Reload** — a changed `max_concurrent` calls `change_global_option`; an
   unchanged one does not; a dead daemon does not fail the save. Plus one live
   test against a real aria2 asserting it took the new value.

### Drift guard

A test asserting every field on `Config`, `General` and `Limits` is reachable
from the settings screen — either as a schema `Field`, or named in an explicit
map of the list-shaped sections:

```python
LIST_SECTIONS = {"categories", "domains", "headers", "proxy_domains"}
```

Anything in neither fails the test. This config grew from 12 keys to 20 in a day;
without the guard the schema falls behind and new settings silently become
uneditable. The exclusion set is deliberately explicit so adding a setting forces
a decision rather than defaulting to invisible.
