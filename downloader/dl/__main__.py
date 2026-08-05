import shutil
import sys
from pathlib import Path

from . import cli, config, daemon, routing, youtube
from .config import CONFIG_FILE
from .rpc import Aria2Error, Aria2Unreachable
from .tui.preview import Request, run_preview

USAGE = """\
dl — download manager

  dl <url> [url...]        queue downloads
  dl -f <file|->           queue URLs from a file or stdin
  dl -d <dir> <url>        override the destination for this download
  dl -p <url>              download through the sing-box proxy
  dl -H "Key: Value"       extra request header (repeatable)
  --no-preview             queue and exit without attaching the live preview
  dl                       open the TUI

  dl ls                    list downloads
  dl history [n]           list finished downloads (--failed, --json)
  dl pause <gid|all>       dl resume <gid|all>      dl rm <gid>
  dl watch                 queue URLs as you copy them
  dl kill                  stop the daemon
"""

SUBCOMMANDS = {"ls", "history", "pause", "resume", "rm", "watch", "kill", "help"}


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(list(argv if argv is not None else sys.argv[1:]))
    except (Aria2Error, Aria2Unreachable) as exc:
        print(f"dl: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def _run_youtube(cfg, urls: list[str], proxy: bool, interactive: bool) -> int:
    """YouTube needs yt-dlp: aria2 cannot resolve a watch page into streams."""
    if shutil.which("yt-dlp") is None:
        print("dl: yt-dlp not found — brew install yt-dlp", file=sys.stderr)
        return 1
    if not interactive:
        print("dl: YouTube downloads need a terminal to choose quality", file=sys.stderr)
        return 1

    from .tui.ytflow import run_youtube

    lines, cancelled = run_youtube(cfg, urls, proxy)
    for line in lines:
        print(line)
    return 130 if cancelled else 0


def _run(args: list[str]) -> int:

    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

    preview = "--no-preview" not in args
    args = [a for a in args if a != "--no-preview"]

    proxy = "-p" in args or "--proxy" in args
    args = [a for a in args if a not in ("-p", "--proxy")]

    headers: list[str] = []
    while "-H" in args:
        at = args.index("-H")
        if at + 1 >= len(args):
            print("dl: -H needs a header, e.g. -H \"Referer: https://site/\"", file=sys.stderr)
            return 1
        headers.append(args[at + 1])
        del args[at : at + 2]

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

    if command == "history":
        # Reading a file needs no daemon, and starting one to print it would be
        # a slow surprise for a command that only looks at the past.
        return cli.cmd_history(cfg, config.STATE_DIR / "history.jsonl", args[1:])

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
    if command == "kill":
        return cli.cmd_kill(client)
    if command == "watch":
        from . import watch

        return watch.run(cfg, client)

    if urls:
        tube = [u for u in urls if youtube.is_youtube(u)]
        urls = [u for u in urls if u not in tube]
        if tube:
            rc = _run_youtube(cfg, tube, proxy, preview and sys.stdout.isatty())
            if rc or not urls:
                return rc

    if urls:
        daemon.bump_generation(config.STATE_DIR)
        interactive = preview and sys.stdout.isatty()
        if not interactive:
            rc, _gids = cli.cmd_add(urls, cfg, client, explicit_dir, proxy=proxy, headers=headers)
            return rc

        if not all(cli.looks_like_url(u) for u in urls):
            rc, gids = cli.cmd_add(urls, cfg, client, explicit_dir, proxy=proxy, headers=headers)
            if gids:
                lines, _cancelled = run_preview(cfg, client, gids=gids)
                for line in lines:
                    print(line)
            return rc

        pending = []
        for url in urls:
            name = routing.filename_from_url(url)
            resolved = routing.resolve(url, name, cfg)
            where = explicit_dir if explicit_dir is not None else resolved.path
            pending.append(Request(url, name or url, where, resolved.category))

        outcome = {"rc": 0}

        def queue(chosen, decisions=None):
            rc, gids = cli.cmd_add(
                urls, cfg, client, explicit_dir, chosen or None, proxy,
                decisions or None, headers,
            )
            outcome["rc"] = rc
            return gids

        lines, cancelled = run_preview(
            cfg, client, pending=pending, queue=queue, pick_paths=explicit_dir is None
        )
        for line in lines:
            print(line)
        return 130 if cancelled else outcome["rc"]

    if not sys.stdout.isatty():
        print("dl: not a terminal — try `dl ls`", file=sys.stderr)
        return 1

    from .tui.app import run_tui

    return run_tui(cfg, client)


if __name__ == "__main__":
    raise SystemExit(main())
