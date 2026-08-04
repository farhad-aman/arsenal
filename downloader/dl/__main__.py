import sys
from pathlib import Path

from . import cli, config, daemon
from .config import CONFIG_FILE

USAGE = """\
dl — download manager

  dl <url> [url...]        queue downloads
  dl -f <file|->           queue URLs from a file or stdin
  dl -d <dir> <url>        override the destination for this download
  dl                       open the TUI

  dl ls                    list downloads
  dl pause <gid|all>       dl resume <gid|all>      dl rm <gid>
  dl limit <rate|off>      global speed limit
  dl watch                 queue URLs as you copy them
  dl kill                  stop the daemon
"""

SUBCOMMANDS = {"ls", "pause", "resume", "rm", "limit", "watch", "kill", "help"}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])

    if args and args[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0

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
    if command == "limit":
        return cli.cmd_limit(args[1] if len(args) > 1 else "off", cfg, client)
    if command == "kill":
        return cli.cmd_kill(client)
    if command == "watch":
        from . import watch

        return watch.run(cfg, client)

    if urls:
        daemon.bump_generation(config.STATE_DIR)
        return cli.cmd_add(urls, cfg, client, explicit_dir)

    if not sys.stdout.isatty():
        print("dl: not a terminal — try `dl ls`", file=sys.stderr)
        return 1

    from .tui.app import run_tui

    return run_tui(cfg, client)


if __name__ == "__main__":
    raise SystemExit(main())
