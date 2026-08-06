from dl import theme
from dl.tui.searchbar import empty_note, summary

AURORA = theme.THEMES["aurora"]
MONO = theme.THEMES["mono"]


def test_summary_names_the_query():
    assert "ubuntu" in summary("ubuntu", 2, 47, AURORA)


def test_summary_counts_matches_against_the_total():
    assert "2 of 47" in summary("ubuntu", 2, 47, AURORA)


def test_summary_without_a_total_says_only_how_many_were_found():
    """The completed log has no bounded size, so a denominator would be a lie."""
    line = summary("ubuntu", 2, None, AURORA)
    assert "2 found" in line
    assert " of " not in line


def test_summary_says_how_to_clear():
    assert "esc" in summary("ubuntu", 2, 47, AURORA)


def test_summary_carries_colour_under_a_colour_theme():
    assert f"[{AURORA.accent}]" in summary("ubuntu", 2, 47, AURORA)


def test_summary_has_no_markup_under_mono():
    assert "[#" not in summary("ubuntu", 2, 47, MONO)


def test_summary_uses_an_ascii_mark_under_mono():
    assert "🔍" not in summary("ubuntu", 2, 47, MONO)


def test_summary_escapes_markup_in_the_query():
    """A query is typed by hand and reaches a markup-enabled widget."""
    assert "\\[bold]" in summary("[bold]", 0, 3, AURORA)


def test_summary_of_no_matches_still_reports_the_total():
    assert "0 of 47" in summary("nothing", 0, 47, AURORA)


def test_empty_note_names_the_query():
    assert "ubuntu" in empty_note("ubuntu", AURORA)


def test_empty_note_escapes_markup_in_the_query():
    assert "\\[bold]" in empty_note("[bold]", AURORA)


def test_empty_note_has_no_markup_under_mono():
    assert "[#" not in empty_note("ubuntu", MONO)
