from pathlib import Path

import tomlkit
from tomlkit.exceptions import ParseError


class BrokenConfig(Exception):
    """The file did not parse. Editing it blind would overwrite whatever the
    user actually wrote with whatever defaults the app fell back to."""

    def __init__(self, message: str, line: int):
        super().__init__(message)
        self.line = line


def read(path: Path):
    try:
        return tomlkit.parse(path.read_text(encoding="utf-8"))
    except ParseError as exc:
        raise BrokenConfig(str(exc), getattr(exc, "line", 1)) from None


def set_value(doc, path: tuple[str, ...], value) -> None:
    table = doc
    for key in path[:-1]:
        if key not in table:
            table[key] = tomlkit.table()
        table = table[key]
    table[path[-1]] = value


def drop(doc, path: tuple[str, ...]) -> None:
    table = doc
    for key in path[:-1]:
        if key not in table:
            return
        table = table[key]
    if path[-1] in table:
        del table[path[-1]]


def write(path: Path, doc) -> None:
    staging = path.with_suffix(path.suffix + ".writing")
    staging.write_text(tomlkit.dumps(doc), encoding="utf-8")
    staging.replace(path)


def apply(path: Path, changes: dict[tuple[str, ...], object]) -> None:
    doc = read(path)
    for where, value in changes.items():
        set_value(doc, where, value)
    write(path, doc)
