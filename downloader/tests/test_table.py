import pytest

from dl import config, theme
from dl.tui.table import Row, bar_width_for, columns_for_width, render_row, row_from_status


@pytest.fixture
def cfg():
    return config.defaults()


@pytest.fixture
def th():
    return theme.THEMES["aurora"]


def status(**over):
    base = {
        "gid": "g1",
        "status": "active",
        "totalLength": "6127219712",
        "completedLength": "4294967296",
        "downloadSpeed": "8493465",
        "connections": "16",
        "files": [{"path": "/tmp/ubuntu.iso", "uris": [{"uri": "https://e.com/ubuntu.iso"}]}],
        "errorMessage": "",
    }
    base.update(over)
    return base


def test_row_from_status_converts_every_numeric_string(cfg):
    row = row_from_status(status(), cfg)
    assert row.total == 6127219712
    assert row.done == 4294967296
    assert row.speed == 8493465
    assert row.conns == 16
    assert all(isinstance(v, int) for v in (row.total, row.done, row.speed, row.conns))


def test_row_from_status_picks_name_and_category(cfg):
    row = row_from_status(status(), cfg)
    assert row.name == "ubuntu.iso"
    assert row.category.name == "iso"


def test_row_from_status_eta_from_speed(cfg):
    row = row_from_status(status(), cfg)
    assert row.eta == (6127219712 - 4294967296) // 8493465


def test_row_from_status_eta_is_negative_when_stalled(cfg):
    row = row_from_status(status(downloadSpeed="0"), cfg)
    assert row.eta < 0


def test_row_from_status_handles_unknown_total(cfg):
    row = row_from_status(status(totalLength="0"), cfg)
    assert row.total == 0
    assert row.eta < 0


def test_row_from_status_missing_files_is_safe(cfg):
    row = row_from_status(status(files=[]), cfg)
    assert row.name == ""
    assert row.category.name == "other"


def test_row_from_status_carries_error_message(cfg):
    row = row_from_status(status(status="error", errorMessage="HTTP 403"), cfg)
    assert row.error == "HTTP 403"


def test_row_pct(cfg):
    assert row_from_status(status(), cfg).pct == pytest.approx(70.09, abs=0.1)


def test_row_pct_is_zero_when_total_unknown(cfg):
    assert row_from_status(status(totalLength="0"), cfg).pct == 0


def test_columns_drop_in_order_as_width_shrinks():
    assert columns_for_width(100) == {"folder", "eta", "spark"}
    assert "folder" not in columns_for_width(78)
    assert "eta" not in columns_for_width(64)
    assert columns_for_width(52) == set()


def test_bar_width_shrinks_but_never_below_four():
    assert bar_width_for(100) >= bar_width_for(60)
    assert bar_width_for(50) >= 4
    assert bar_width_for(20) >= 4


def test_render_row_produces_two_lines_when_unselected(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    assert len(lines) == 2


def test_render_row_selected_but_collapsed_is_still_two_lines(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=True, frame=0)
    assert len(lines) == 2


def test_render_row_selected_and_expanded_adds_detail_line(cfg, th):
    lines = render_row(
        row_from_status(status(), cfg), th, 100, selected=True, frame=0, expanded=True
    )
    assert len(lines) == 3
    assert "/tmp/ubuntu.iso" in lines[2]
    assert "16 conns" in lines[2]


def test_render_row_expanded_but_unselected_adds_nothing(cfg, th):
    lines = render_row(
        row_from_status(status(), cfg), th, 100, selected=False, frame=0, expanded=True
    )
    assert len(lines) == 2


def test_row_is_not_proxied_by_default(cfg):
    assert row_from_status(status(), cfg).proxied is False


def test_row_carries_the_proxy_flag(cfg):
    assert row_from_status(status(), cfg, proxied=True).proxied is True


def test_render_row_badges_a_proxied_download(cfg, th):
    proxied = render_row(row_from_status(status(), cfg, proxied=True), th, 100, False, 0)
    assert "🌐" in proxied[0]


def test_render_row_leaves_a_direct_download_unbadged(cfg, th):
    direct = render_row(row_from_status(status(), cfg), th, 100, False, 0)
    assert "🌐" not in direct[0]


def test_render_row_badges_without_emoji_in_a_mono_theme(cfg):
    from dl import theme

    mono = theme.THEMES["mono"]
    proxied = render_row(row_from_status(status(), cfg, proxied=True), mono, 100, False, 0)
    assert "🌐" not in proxied[0]
    assert theme.GLYPHS["🌐"] in proxied[0]


def test_render_row_keeps_its_two_line_shape_when_proxied(cfg, th):
    assert len(render_row(row_from_status(status(), cfg, proxied=True), th, 100, False, 0)) == 2


def test_render_row_selected_gets_accent_marker(cfg, th):
    selected = render_row(row_from_status(status(), cfg), th, 100, selected=True, frame=0)
    plain = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    assert "▌" in selected[0]
    assert "▌" not in plain[0]


def test_render_row_shows_name_and_sizes(cfg, th):
    lines = render_row(row_from_status(status(), cfg), th, 100, selected=False, frame=0)
    joined = " ".join(lines)
    assert "ubuntu.iso" in joined
    assert "5.7 GB" in joined
    assert "70%" in joined


def test_render_row_paused_shows_paused_not_speed(cfg, th):
    row = row_from_status(status(status="paused", downloadSpeed="0"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=0))
    assert "paused" in joined


def test_render_row_error_shows_message_and_retry_hint(cfg, th):
    row = row_from_status(status(status="error", errorMessage="HTTP 403"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=0))
    assert "HTTP 403" in joined
    assert "retry" in joined


def test_render_row_mono_theme_has_no_color_markup(cfg):
    row = row_from_status(status(), cfg)
    lines = render_row(row, theme.THEMES["mono"], 100, selected=False, frame=0)
    assert "[#" not in " ".join(lines)


def test_render_row_narrow_hides_folder(cfg, th):
    row = row_from_status(status(), cfg)
    joined = " ".join(render_row(row, th, 60, selected=False, frame=0))
    assert "ISO" not in joined


def test_render_row_queued_uses_spinner_frame(cfg, th):
    from dl.format import SPINNER

    row = row_from_status(status(status="waiting", downloadSpeed="0"), cfg)
    joined = " ".join(render_row(row, th, 100, selected=False, frame=3))
    assert SPINNER[3] in joined


def yt_job(**over):
    base = {
        "id": "yt-1",
        "url": "https://youtu.be/abc",
        "dir": "/tmp",
        "status": "active",
        "title": "clip",
        "choices": {"container": "mp4"},
        "total": 1000,
        "done": 500,
        "speed": 100,
        "proxy": "",
    }
    base.update(over)
    return base


def test_row_from_job_marks_a_proxied_youtube_download(cfg):
    from dl.tui.table import row_from_job

    assert row_from_job(yt_job(proxy="http://127.0.0.1:2080"), cfg).proxied is True


def test_row_from_job_leaves_a_direct_youtube_download_unbadged(cfg):
    from dl.tui.table import row_from_job

    assert row_from_job(yt_job(), cfg).proxied is False


def test_render_row_escapes_markup_in_filename(cfg, th):
    row = row_from_status(
        status(files=[{"path": "/tmp/[bold]sneaky.iso", "uris": []}]), cfg
    )
    joined = " ".join(render_row(row, th, 100, selected=False, frame=0))
    assert "\\[bold]" in joined
