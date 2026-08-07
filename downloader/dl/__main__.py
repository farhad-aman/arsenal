import argparse
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
  dl <magnet:…>            magnet link
  dl file.torrent          torrent file, local or over http
  --no-preview             queue and exit without attaching the live preview
  dl                       open the TUI

  dl ls [name]             list downloads, optionally matching a name (--json)
  dl history [n] [name]    list finished downloads (--failed, --json)
  dl pause <gid|all>       dl resume <gid|all>      dl rm <gid>
  dl watch                 queue URLs as you copy them
  dl kill                  stop the daemon
  dl kill --strays         find daemons older versions left behind
"""

SUBCOMMANDS = {"ls", "history", "pause", "resume", "rm", "watch", "kill", "help"}

# Flags each subcommand understands. Anything else is a mistake worth saying so
# about rather than dropping: `dl ls --failed` used to filter nothing, quietly.
SUBCOMMAND_FLAGS = {
    "kill": {"--strays"},
    "history": {"--failed", "--json"},
    "ls": {"--json"},
}


class ArgError(Exception):
    """A bad command line. Carries the message dl should print."""


class _Parser(argparse.ArgumentParser):
    """argparse that reports rather than exits.

    The stock parser calls sys.exit(2) from inside error(), which would bypass
    main()'s return codes and print a usage block dl does not own.
    """

    def error(self, message: str):
        raise ArgError(message)

    def exit(self, status: int = 0, message: str | None = None):
        raise ArgError(message or "")


def _add_parser() -> _Parser:
    parser = _Parser(prog="dl", add_help=False)
    parser.add_argument("urls", nargs="*")
    parser.add_argument("-d", "--dir", dest="directory")
    parser.add_argument("-f", "--file", dest="url_file")
    parser.add_argument("-p", "--proxy", action="store_true")
    parser.add_argument("-H", dest="headers", action="append", default=[])
    parser.add_argument("--no-preview", dest="preview", action="store_false", default=True)
    return parser


def _check_flags(command: str, rest: list[str]) -> None:
    allowed = SUBCOMMAND_FLAGS.get(command, set())
    for arg in rest:
        if arg.startswith("-") and arg not in allowed:
            raise ArgError(f"unrecognized argument for {command}: {arg}")


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(list(argv if argv is not None else sys.argv[1:]))
    except ArgError as exc:
        print(f"dl: {exc}\n\n{USAGE}", file=sys.stderr)
        return 1
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

    command = args[0] if args and args[0] in SUBCOMMANDS else None
    preview, proxy, headers = True, False, []
    explicit_dir: Path | None = None
    urls: list[str] = []

    if command is not None:
        _check_flags(command, args[1:])
    else:
        opts = _add_parser().parse_args(args)
        preview, proxy, headers = opts.preview, opts.proxy, opts.headers
        explicit_dir = Path(opts.directory).expanduser() if opts.directory else None
        if opts.url_file:
            try:
                urls = cli.read_url_file(opts.url_file)
            except OSError as exc:
                raise ArgError(str(exc)) from None
        urls += list(opts.urls)

    if not CONFIG_FILE.exists():
        config.write_default(CONFIG_FILE)
    cfg = config.load(CONFIG_FILE)

    if command == "kill" and "--strays" in args:
        # Starting a daemon in order to list the ones that should not exist
        # would be a poor answer to the question.
        return cli.cmd_strays(config.STATE_DIR)

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
        query = " ".join(a for a in args[1:] if not a.startswith("-"))
        return cli.cmd_ls(
            cfg,
            client,
            use_color=sys.stdout.isatty(),
            query=query,
            as_json="--json" in args,
        )
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
        return cli.cmd_kill(client, config.STATE_DIR)
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
