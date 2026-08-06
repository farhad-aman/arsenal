import pytest

from dl import sort


def order(field="queue", reverse=False):
    return sort.Order(field, reverse)


def test_the_active_fields_start_at_queue_order():
    assert sort.FIELDS[0] == "queue"


def test_the_completed_fields_start_at_recent():
    assert sort.DONE_FIELDS[0] == "recent"


def test_speed_and_progress_are_active_only():
    """A finished download has neither."""
    assert "speed" not in sort.DONE_FIELDS
    assert "progress" not in sort.DONE_FIELDS


def test_cycling_steps_through_every_field_and_returns():
    seen = [sort.DEFAULT.field]
    current = sort.DEFAULT
    for _ in range(len(sort.FIELDS) - 1):
        current = sort.next_field(current, sort.FIELDS)
        seen.append(current.field)
    assert seen == list(sort.FIELDS)
    assert sort.next_field(current, sort.FIELDS).field == sort.FIELDS[0]


def test_cycling_to_a_size_field_starts_descending():
    """Pressing S to reach size should show the biggest first, unaided."""
    for field in ("size", "speed", "progress"):
        assert sort.for_field(field).reverse is True


def test_cycling_to_name_starts_ascending():
    assert sort.for_field("name").reverse is False
    assert sort.for_field("queue").reverse is False


def test_recent_starts_unreversed_because_the_log_arrives_newest_first():
    assert sort.for_field("recent").reverse is False


def test_cycling_a_field_resets_the_direction():
    """Otherwise a flip made on speed silently follows you to progress."""
    flipped = sort.flipped(sort.for_field("name"))
    assert flipped.reverse is True
    assert sort.next_field(flipped, sort.FIELDS).reverse is True  # size


def test_flipping_reverses_and_keeps_the_field():
    flipped = sort.flipped(order("size", True))
    assert flipped == sort.Order("size", False)


def test_flipping_twice_is_the_original():
    start = sort.for_field("speed")
    assert sort.flipped(sort.flipped(start)) == start


def test_cycling_from_an_unknown_field_lands_on_the_first():
    assert sort.next_field(order("nonsense"), sort.FIELDS).field == sort.FIELDS[0]


class FakeRow:
    def __init__(self, name, total=0, speed=0, done=0):
        self.name = name
        self.total = total
        self.speed = speed
        self.done = done

    @property
    def pct(self):
        return (self.done * 100.0 / self.total) if self.total else 0.0

    def __repr__(self):
        return f"<{self.name}>"


def rows():
    return [
        FakeRow("charlie.iso", total=100, speed=50, done=90),
        FakeRow("alpha.iso", total=300, speed=10, done=30),
        FakeRow("bravo.iso", total=200, speed=90, done=20),
    ]


def named(sorted_rows):
    return [r.name for r in sorted_rows]


def test_queue_order_leaves_the_rows_alone():
    assert named(sort.apply_rows(rows(), order("queue"))) == [
        "charlie.iso",
        "alpha.iso",
        "bravo.iso",
    ]


def test_queue_order_reversed_flips_the_queue():
    assert named(sort.apply_rows(rows(), order("queue", True))) == [
        "bravo.iso",
        "alpha.iso",
        "charlie.iso",
    ]


def test_sorting_by_name():
    assert named(sort.apply_rows(rows(), order("name"))) == [
        "alpha.iso",
        "bravo.iso",
        "charlie.iso",
    ]


def test_sorting_by_name_ignores_case():
    mixed = [FakeRow("Beta.iso"), FakeRow("alpha.iso")]
    assert named(sort.apply_rows(mixed, order("name"))) == ["alpha.iso", "Beta.iso"]


def test_sorting_by_name_normalises_unicode():
    """The same normalisation search uses, so the two agree on one filename."""
    import unicodedata

    decomposed = unicodedata.normalize("NFD", "café.mkv")
    listed = [FakeRow("zzz.iso"), FakeRow(decomposed)]
    assert named(sort.apply_rows(listed, order("name")))[0] == decomposed


def test_sorting_by_size_descending():
    assert named(sort.apply_rows(rows(), order("size", True))) == [
        "alpha.iso",
        "bravo.iso",
        "charlie.iso",
    ]


def test_sorting_by_speed_descending():
    assert named(sort.apply_rows(rows(), order("speed", True))) == [
        "bravo.iso",
        "charlie.iso",
        "alpha.iso",
    ]


def test_sorting_by_progress_descending():
    assert named(sort.apply_rows(rows(), order("progress", True))) == [
        "charlie.iso",
        "alpha.iso",
        "bravo.iso",
    ]


def test_sorting_is_stable_so_queue_order_breaks_ties():
    tied = [FakeRow("first", total=10), FakeRow("second", total=10)]
    assert named(sort.apply_rows(tied, order("size", True))) == ["first", "second"]


def test_sorting_an_empty_list_is_empty():
    assert sort.apply_rows([], order("size", True)) == []


def test_sorting_does_not_mutate_the_list_it_is_given():
    original = rows()
    sort.apply_rows(original, order("name"))
    assert named(original) == ["charlie.iso", "alpha.iso", "bravo.iso"]


def records():
    return [
        {"name": "charlie.iso", "bytes": 100, "ts": 300},
        {"name": "alpha.iso", "bytes": 300, "ts": 100},
        {"name": "bravo.iso", "bytes": 200, "ts": 200},
    ]


def test_records_sort_by_size():
    got = sort.apply_records(records(), order("size", True))
    assert [r["name"] for r in got] == ["alpha.iso", "bravo.iso", "charlie.iso"]


def test_records_sort_by_name():
    got = sort.apply_records(records(), order("name"))
    assert [r["name"] for r in got] == ["alpha.iso", "bravo.iso", "charlie.iso"]


def test_records_recent_order_leaves_them_alone():
    """The tab already hands them over newest first."""
    got = sort.apply_records(records(), order("recent"))
    assert [r["name"] for r in got] == ["charlie.iso", "alpha.iso", "bravo.iso"]


def test_records_survive_missing_keys():
    got = sort.apply_records([{"name": "a.iso"}, {"bytes": 5}], order("size", True))
    assert len(got) == 2


@pytest.mark.parametrize(
    "field,reverse,expected",
    [
        ("queue", False, "queue"),
        ("size", True, "size ↓"),
        ("size", False, "size ↑"),
        ("name", False, "name ↑"),
    ],
)
def test_label_names_the_field_and_direction(field, reverse, expected):
    assert sort.label(sort.Order(field, reverse), icons=True) == expected


def test_label_of_the_default_is_plain_queue():
    """No arrow on the default: there is nothing to compare it against."""
    assert sort.label(sort.DEFAULT, icons=True) == "queue"


def test_label_uses_ascii_arrows_without_icons():
    plain = sort.label(sort.Order("size", True), icons=False)
    assert "↓" not in plain
    assert "size" in plain


def test_sorted_is_true_only_away_from_the_default():
    assert sort.sorted_away(sort.DEFAULT) is False
    assert sort.sorted_away(sort.Order("queue", True)) is True
    assert sort.sorted_away(sort.Order("size", True)) is True


def test_each_tab_has_its_own_resting_order():
    """Completed rests at recent, not queue, so it must not announce a sort it
    is not in."""
    assert sort.sorted_away(sort.DONE_DEFAULT) is False
    assert sort.label(sort.DONE_DEFAULT, icons=True) == "recent"
