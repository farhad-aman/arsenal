import pytest

from dl import theme
from dl.tui.status import render_status, stats_from


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


def test_render_status_has_no_global_limit_indicator(th):
    """Speed limits are per-download only; a global indicator would be a lie."""
    out = render_status(stats_from(gstat(), 261), [1, 2, 3], th, 100)
    assert "🚦" not in out
    assert "limit" not in out.lower()
