import unicodedata

from dl import search


def test_an_empty_query_matches_everything():
    assert search.matches("ubuntu.iso", "")
    assert search.matches("", "")


def test_a_substring_matches_anywhere_in_the_name():
    assert search.matches("ubuntu-24.04.iso", "24.04")
    assert search.matches("ubuntu-24.04.iso", "ubuntu")
    assert search.matches("ubuntu-24.04.iso", "iso")


def test_a_query_that_is_not_there_does_not_match():
    assert not search.matches("ubuntu-24.04.iso", "debian")


def test_matching_ignores_case_in_both_directions():
    assert search.matches("Ubuntu.ISO", "ubuntu")
    assert search.matches("ubuntu.iso", "UBUNTU")


def test_matching_ignores_surrounding_whitespace_in_the_query():
    assert search.matches("ubuntu.iso", "  ubuntu  ")


def test_a_decomposed_filename_matches_a_composed_query():
    """macOS stores filenames decomposed, and a terminal sends what you typed
    composed. Without normalising, searching your own downloads finds nothing."""
    on_disk = unicodedata.normalize("NFD", "قهرمان.mp4")
    typed = unicodedata.normalize("NFC", "قهرمان")
    assert on_disk != typed
    assert search.matches(on_disk, typed)


def test_a_composed_filename_matches_a_decomposed_query():
    on_disk = unicodedata.normalize("NFC", "café.mp4")
    typed = unicodedata.normalize("NFD", "café")
    assert search.matches(on_disk, typed)


def test_a_missing_name_matches_nothing_but_the_empty_query():
    assert search.matches("", "") is True
    assert search.matches("", "ubuntu") is False


def test_keep_filters_by_the_key_it_is_given():
    items = [{"name": "ubuntu.iso"}, {"name": "debian.iso"}]
    kept = search.keep(items, "ubuntu", lambda item: item["name"])
    assert kept == [{"name": "ubuntu.iso"}]


def test_keep_returns_everything_for_an_empty_query():
    items = [{"name": "ubuntu.iso"}, {"name": "debian.iso"}]
    assert search.keep(items, "", lambda item: item["name"]) == items


def test_keep_holds_the_order_it_was_given():
    items = [{"name": "b-ubuntu"}, {"name": "a-ubuntu"}, {"name": "c-ubuntu"}]
    kept = search.keep(items, "ubuntu", lambda item: item["name"])
    assert [item["name"] for item in kept] == ["b-ubuntu", "a-ubuntu", "c-ubuntu"]


def test_keep_survives_a_key_that_returns_none():
    items = [{"name": None}, {"name": "ubuntu.iso"}]
    kept = search.keep(items, "ubuntu", lambda item: item["name"])
    assert kept == [{"name": "ubuntu.iso"}]


def test_active_reports_whether_a_query_is_worth_applying():
    assert search.active("ubuntu") is True
    assert search.active("") is False
    assert search.active("   ") is False
