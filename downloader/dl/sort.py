from dataclasses import dataclass

from .search import normalise

FIELDS = ("queue", "name", "size", "speed", "progress")
DONE_FIELDS = ("recent", "name", "size")

# Reaching one of these by pressing S should already show the interesting end
# of the list, without also having to flip the direction. "recent" is absent
# because the completed log already arrives newest first.
_DESCENDING = frozenset({"size", "speed", "progress"})

# The order rows arrive in, which is queue order for aria2 and newest-first for
# the completed log. Sorting by it would be a no-op, so it is the identity.
_UNSORTED = frozenset({"queue", "recent"})


@dataclass(frozen=True)
class Order:
    field: str
    reverse: bool


DEFAULT = Order(FIELDS[0], False)
DONE_DEFAULT = Order(DONE_FIELDS[0], False)


def for_field(field: str) -> Order:
    return Order(field, field in _DESCENDING)


def next_field(order: Order, fields: tuple[str, ...]) -> Order:
    at = fields.index(order.field) if order.field in fields else -1
    return for_field(fields[(at + 1) % len(fields)])


def flipped(order: Order) -> Order:
    return Order(order.field, not order.reverse)


def sorted_away(order: Order) -> bool:
    """Whether the list is in an order worth announcing."""
    return order not in (DEFAULT, DONE_DEFAULT)


def label(order: Order, icons: bool) -> str:
    if not order.reverse and order.field in _UNSORTED:
        return order.field
    arrow = ("↓" if order.reverse else "↑") if icons else ("v" if order.reverse else "^")
    return f"{order.field} {arrow}"


def _row_key(field: str):
    if field == "name":
        return lambda row: normalise(row.name)
    if field == "size":
        return lambda row: row.total
    if field == "speed":
        return lambda row: row.speed
    return lambda row: row.pct


def _record_key(field: str):
    if field == "name":
        return lambda record: normalise(record.get("name") or "")
    return lambda record: int(record.get("bytes") or 0)


def _apply(items: list, order: Order, key) -> list:
    """Stable, so equal values keep the order they arrived in."""
    if order.field in _UNSORTED:
        return list(reversed(items)) if order.reverse else list(items)
    return sorted(items, key=key, reverse=order.reverse)


def apply_rows(rows: list, order: Order) -> list:
    return _apply(rows, order, _row_key(order.field))


def apply_records(records: list[dict], order: Order) -> list[dict]:
    return _apply(records, order, _record_key(order.field))
