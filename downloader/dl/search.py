import unicodedata
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def normalise(text: str) -> str:
    """macOS writes filenames decomposed and terminals send composed text, so
    the same Persian or accented name compares unequal until both are NFC."""
    return unicodedata.normalize("NFC", text or "").strip().casefold()


def active(query: str) -> bool:
    return bool(normalise(query))


def matches(text: str, query: str) -> bool:
    needle = normalise(query)
    if not needle:
        return True
    return needle in normalise(text)


def keep(items: Iterable[T], query: str, key: Callable[[T], str | None]) -> list[T]:
    if not active(query):
        return list(items)
    return [item for item in items if matches(key(item) or "", query)]
