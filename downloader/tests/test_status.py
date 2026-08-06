import re

import pytest

from dl import theme
from dl.format import cells
from dl.tui.status import render_status, stats_from

_MARKUP = re.compile(r"\[[^]]*\]")


@pytest.fixture
def th():
    return theme.THEMES["aurora"]


def gstat(**over):
    base = {"downloadSpeed": "13002342", "numActive": "3", "numWaiting": "2", "numStopped": "47"}
    base.update(over)
    return base


def test_stats_from_converts_strings_to_ints():
    s = stats_from(gstat(), 261)
    assert (s.speed, s.active, s.waiting, s.done) == (13002342, 3, 2, 47)
    assert all(isinstance(v, int) for v in (s.speed, s.active, s.waiting, s.done))


def test_stats_from_handles_missing_keys():
    s = stats_from({}, 0)
    assert (s.speed, s.active, s.waiting, s.done) == (0, 0, 0, 0)


def test_render_status_shows_speed_and_counts(th):
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100)
    assert "12.4 MB/s" in out
    assert "3" in out and "2" in out and "47" in out




def test_render_status_includes_elapsed(th):
    out = render_status(stats_from(gstat(), 261), [], th, 100)
    assert "4m 21s" in out


def test_render_status_mono_has_no_color_markup():
    out = render_status(stats_from(gstat(), 0), [1, 2], theme.THEMES["mono"], 100)
    assert "[#" not in out


def test_render_status_narrow_still_fits(th):
    out = render_status(stats_from(gstat(), 0), [1, 2, 3], th, 50)
    assert out


def test_render_status_graph_shrinks_with_width(th):
    stats = stats_from(gstat(), 0)
    wide = render_status(stats, [1, 2, 3], theme.THEMES["mono"], 100)
    narrow = render_status(stats, [1, 2, 3], theme.THEMES["mono"], 50)
    assert len(wide) > len(narrow)


def test_render_status_handles_empty_history(th):
    assert render_status(stats_from(gstat(), 0), [], th, 100)


def test_render_status_shows_the_sort_badge(th):
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100, "size ↓")
    assert "size ↓" in out


def test_render_status_without_a_sort_label_shows_no_badge(th):
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100)
    assert "⇅" not in out


def test_the_badge_does_not_push_the_elapsed_time_off_the_bar(th):
    """The badge costs its own width plus the gap after it. Budgeting only the
    first clips the reading at the far end."""
    for label in ("queue", "progress ↓", "size ↓"):
        out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100, label)
        assert "4m 21s" in out, label
        assert cells(_MARKUP.sub("", out)) <= 100, label


def test_the_badge_is_dropped_when_there_is_no_room_for_it(th):
    """The counters already overflow a 60-column bar on their own. The badge
    gives up its width rather than making that worse."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 60, "progress ↓")
    assert "progress" not in out
    assert "4m 21s" in out


def test_the_badge_survives_a_width_that_can_hold_it(th):
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 120, "progress ↓")
    assert "progress ↓" in out
    assert cells(_MARKUP.sub("", out)) <= 120


def test_render_status_has_no_global_limit_indicator(th):
    """Speed limits are per-download only; a global indicator would be a lie."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100)
    assert "🚦" not in out
    assert "limit" not in out.lower()


def plain(line: str) -> int:
    return cells(_MARKUP.sub("", line))


@pytest.mark.parametrize("width", [120, 100, 90, 80, 70, 64, 56, 48, 40, 32, 24])
def test_the_bar_never_runs_off_the_edge(th, width):
    """It used to overflow below about 70 columns: the counters alone were
    wider than the terminal, and nothing gave way."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, width, "progress ↓")
    assert plain(out) <= width, f"{width}: {plain(out)} cells\n{out}"


@pytest.mark.parametrize("width", [120, 100, 80, 64, 48, 32])
def test_the_bar_fits_in_mono_too(width):
    mono = theme.THEMES["mono"]
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], mono, width, "size v")
    assert plain(out) <= width


def test_the_speed_survives_every_width(th):
    """It is the one reading worth keeping when there is room for one."""
    for width in (120, 80, 60, 40, 30):
        assert "12.4 MB/s" in render_status(stats_from(gstat(), 261), [], th, width)


def test_counters_give_up_their_words_before_their_numbers(th):
    """A narrow bar still says how many are active, just not in prose."""
    out = render_status(stats_from(gstat(), 261), [], th, 56)
    assert "3" in out
    assert "active" not in out


def test_a_wide_bar_keeps_the_words(th):
    out = render_status(stats_from(gstat(), 261), [], th, 120)
    assert "active" in out
    assert "queued" in out


def test_the_elapsed_time_goes_before_the_counters_do(th):
    """Which is still running matters more than how long dl has been open."""
    out = render_status(stats_from(gstat(), 261), [], th, 44)
    assert "3" in out
    assert "4m 21s" not in out


def test_the_graph_never_costs_a_word(th):
    """It is the first thing given up. At a width where the counters can still
    speak, they do."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 76)
    assert "active" in out
    assert "▁" not in out


def test_the_graph_fills_room_nothing_else_wanted(th):
    """The layout is chosen before the graph, so leftover space is free."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 64)
    assert "▁" in out or "▃" in out
    assert plain(out) <= 64


def test_a_bar_with_no_counters_has_no_graph(th):
    """Nothing left to annotate, and the speed is what the room is for."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 30)
    assert "▁" not in out
    assert "12.4 MB/s" in out


def test_the_bar_survives_a_width_of_one(th):
    assert render_status(stats_from(gstat(), 0), [1], th, 1)
