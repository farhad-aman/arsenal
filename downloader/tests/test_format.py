import pytest

from dl.format import (
    BLOCKS,
    SPINNER,
    human_bytes,
    human_duration,
    human_speed,
    progress_bar,
    sparkline,
)


@pytest.mark.parametrize(
    "n,expected",
    [
        (0, "0 B"),
        (512, "512 B"),
        (1023, "1023 B"),
        (1024, "1.0 KB"),
        (2202009, "2.1 MB"),
        (922746880, "880 MB"),
        (6127219712, "5.7 GB"),
        (12884901888, "12 GB"),
    ],
)
def test_human_bytes(n, expected):
    assert human_bytes(n) == expected


def test_human_bytes_negative_is_dash():
    assert human_bytes(-1) == "—"


@pytest.mark.parametrize(
    "s,expected",
    [(0, "0s"), (3, "3s"), (59, "59s"), (201, "3m 21s"), (302, "5m 02s"), (683, "11m 23s"), (5025, "1h 23m")],
)
def test_human_duration(s, expected):
    assert human_duration(s) == expected


def test_human_duration_negative_is_dash():
    assert human_duration(-1) == "—"


def test_human_speed_appends_per_second():
    assert human_speed(8493465) == "8.1 MB/s"
    assert human_speed(0) == "0 B/s"


def test_human_speed_keeps_a_decimal_above_ten_unlike_human_bytes():
    assert human_speed(13002342) == "12.4 MB/s"
    assert human_bytes(13002342) == "12 MB"


def test_human_speed_negative_is_zero():
    assert human_speed(-5) == "0 B/s"


def test_sparkline_uses_all_eight_levels_across_a_ramp():
    line = sparkline(list(range(8)), 8)
    assert line == BLOCKS


def test_sparkline_flat_zero_is_lowest_block():
    assert sparkline([0, 0, 0], 3) == BLOCKS[0] * 3


def test_sparkline_pads_left_when_short_of_width():
    assert sparkline([7], 4) == BLOCKS[0] * 3 + BLOCKS[7]


def test_sparkline_keeps_most_recent_when_over_width():
    assert sparkline([0, 0, 0, 7], 2) == BLOCKS[0] + BLOCKS[7]


def test_sparkline_zero_width_is_empty():
    assert sparkline([1, 2, 3], 0) == ""


def test_progress_bar_full_is_all_solid():
    assert progress_bar(100.0, 10) == "█" * 10


def test_progress_bar_empty_has_no_solid_and_no_comet():
    assert progress_bar(0.0, 10) == "░" * 10


def test_progress_bar_has_comet_tail_after_body():
    bar = progress_bar(50.0, 10)
    assert bar == "█████▓▒░░░"
    assert len(bar) == 10


def test_progress_bar_comet_truncates_near_the_end():
    assert progress_bar(90.0, 10) == "█████████▓"


@pytest.mark.parametrize("width", range(4, 41))
@pytest.mark.parametrize("pct", [0, 1, 33.3, 50, 66.6, 99, 100])
def test_progress_bar_always_exact_width(width, pct):
    assert len(progress_bar(pct, width)) == width


def test_progress_bar_clamps_out_of_range():
    assert progress_bar(150.0, 5) == "█" * 5
    assert progress_bar(-10.0, 5) == "░" * 5


def test_spinner_has_ten_frames():
    assert len(SPINNER) == 10


def test_cells_counts_ascii_as_one_each():
    from dl.format import cells

    assert cells("ubuntu.iso") == 10


def test_cells_counts_an_emoji_as_two():
    """Python's len() says one, the terminal draws two. Padding by len is why
    every column after a name with emoji in it drifts."""
    from dl.format import cells

    assert cells("🎬") == 2
    assert cells("a🎬b") == 4


def test_cells_counts_fullwidth_punctuation_as_two():
    from dl.format import cells

    assert cells("｜") == 2


def test_cells_ignores_combining_marks():
    from dl.format import cells

    assert cells("é") == 1


def test_pad_fills_to_a_true_column_width():
    from dl.format import cells, pad

    for text in ("plain", "🎬 clip", "دختره💔", "Toxicity ｜ x"):
        assert cells(pad(text, 24)) == 24, text


def test_pad_leaves_text_that_is_already_too_wide():
    from dl.format import pad

    assert pad("x" * 30, 10) == "x" * 30


def test_rpad_right_aligns_to_a_true_column_width():
    from dl.format import cells, rpad

    assert cells(rpad("🎬 5 GB", 20)) == 20
    assert rpad("ok", 6).endswith("ok")


def test_trim_cuts_to_a_true_width():
    from dl.format import cells, trim

    assert cells(trim("🎬🎬🎬🎬", 5)) <= 5
    assert trim("short", 20) == "short"


def test_trim_never_splits_a_wide_glyph_in_half():
    from dl.format import cells, trim

    assert cells(trim("🎬🎬", 3)) == 2
