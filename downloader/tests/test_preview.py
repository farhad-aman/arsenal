from dl.tui.preview import summarise


def result(**over):
    base = {"name": "a.iso", "status": "complete", "bytes": 6127219712, "seconds": 683, "error": ""}
    base.update(over)
    return base


def test_summarise_empty_is_empty():
    assert summarise([]) == []


def test_summarise_success_shows_size_duration_and_average():
    line = summarise([result()])[0]
    assert "✅" in line
    assert "a.iso" in line
    assert "5.7 GB" in line
    assert "11m 23s" in line
    assert "8.6 MB/s" in line


def test_summarise_success_without_duration_omits_the_average():
    line = summarise([result(seconds=0)])[0]
    assert "5.7 GB" in line
    assert "/s" not in line


def test_summarise_error_shows_the_message():
    line = summarise([result(status="error", error="HTTP 403")])[0]
    assert "❌" in line
    assert "a.iso" in line
    assert "HTTP 403" in line


def test_summarise_error_without_message_says_failed():
    assert "failed" in summarise([result(status="error", error="")])[0]


def test_summarise_removed_is_reported():
    line = summarise([result(status="removed")])[0]
    assert "a.iso" in line
    assert "removed" in line


def test_summarise_running_collapses_into_one_trailing_line():
    lines = summarise([result(status="active"), result(name="b.mkv", status="waiting")])
    assert len(lines) == 1
    assert "2 still downloading" in lines[0]
    assert "dl ls" in lines[0]


def test_summarise_singular_wording_for_one_running():
    assert "1 still downloading" in summarise([result(status="active")])[0]


def test_summarise_mixed_lists_finished_then_running():
    lines = summarise(
        [
            result(),
            result(name="b.mkv", status="error", error="boom"),
            result(name="c.zip", status="active"),
        ]
    )
    assert len(lines) == 3
    assert "a.iso" in lines[0]
    assert "b.mkv" in lines[1]
    assert "1 still downloading" in lines[2]


def test_summarise_ascii_mode_uses_no_emoji():
    lines = summarise(
        [
            result(),
            result(name="b.mkv", status="error", error="boom"),
            result(name="c.zip", status="active"),
        ],
        icons=False,
    )
    joined = " ".join(lines)
    assert "✅" not in joined and "❌" not in joined and "⏳" not in joined
    assert "[ok]" in joined
    assert "[fail]" in joined
    assert "[...]" in joined


def test_summarise_never_emits_markup_that_would_break_a_terminal():
    for line in summarise([result(), result(status="active")]):
        assert "\x1b[" not in line


def test_summarise_unnamed_result_has_a_placeholder():
    assert "(unnamed)" in summarise([result(name="")])[0]
