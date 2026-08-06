import pytest
from textual.widgets import Input

from dl import history
from dl.tui import app as app_module
from dl.tui.app import DlApp
from dl.tui.searchbar import INPUT_ID
from tests.test_app import FakeClient


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


@pytest.fixture(autouse=True)
def state(monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path)
    return tmp_path


async def type_query(pilot, text: str) -> None:
    await pilot.press("slash")
    await pilot.pause()
    for ch in text:
        await pilot.press(ch)
    await pilot.pause()


def names(app) -> list[str]:
    return [row.name for row in app.table.rows]


async def test_slash_opens_the_box_and_focuses_it(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.searching is False
        await pilot.press("slash")
        await pilot.pause()
        assert app.searching is True
        assert app.focused is app.search_input


async def test_typing_filters_the_active_table_live(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert sorted(names(app)) == ["a.iso", "b.mkv"]
        await type_query(pilot, "iso")
        await app.refresh_data()
        assert names(app) == ["a.iso"]


async def test_the_filter_survives_committing_with_enter(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("enter")
        await pilot.pause()
        assert app.searching is False
        assert app.search_query == "iso"
        await app.refresh_data()
        assert names(app) == ["a.iso"]


async def test_escape_clears_the_filter_and_closes_the_box(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("escape")
        await pilot.pause()
        assert app.searching is False
        assert app.search_query == ""
        await app.refresh_data()
        assert sorted(names(app)) == ["a.iso", "b.mkv"]


async def test_escape_clears_a_filter_committed_with_enter(cfg):
    """The box carries its own escape binding, and enter unmounts the box. Only
    testing escape while it is open leaves the committed filter unclearable."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("enter")
        await pilot.pause()
        assert app.search_query == "iso"
        await pilot.press("escape")
        await pilot.pause()
        assert app.search_query == ""
        await app.refresh_data()
        assert sorted(names(app)) == ["a.iso", "b.mkv"]
        assert app.search_note.display is False


async def test_escape_clears_a_committed_filter_on_the_completed_tab(cfg, state):
    log = state / "history.jsonl"
    for name in ("ubuntu.iso", "clip.mp4"):
        history.append({"name": name, "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("enter")
        await pilot.pause()
        assert len(app.completed.rows) == 1
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.completed.rows) == 2


async def test_the_note_reports_matches_against_the_total(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await app.refresh_data()
        assert app.search_note.display is True
        assert "1 of 2" in app.search_note.text


async def test_the_note_is_hidden_when_no_query_is_active(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        assert app.search_note.display is False


async def test_no_match_says_so_instead_of_showing_the_splash(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "nothingmatchesthis")
        await app.refresh_data()
        assert names(app) == []
        assert "nothing matches" in app.table.placeholder
        assert "nothingmatchesthis" in app.table.text


async def test_the_empty_message_names_the_current_query_not_the_last_one(cfg):
    """set_rows draws the placeholder, so assigning it afterwards leaves the
    message describing the query before this one."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await app.refresh_data()
        for ch in "zzz":
            await pilot.press(ch)
        await pilot.pause()
        await app.refresh_data()
        assert names(app) == []
        assert 'nothing matches "isozzz"' in app.table.text


async def test_reopening_the_box_keeps_the_committed_query(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.search_input.value == "iso"


async def test_a_letter_typed_into_the_box_does_not_trigger_its_binding(cfg):
    """The regression the settings screen taught: a focused Input must eat d,
    not have it open the delete modal underneath."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await type_query(pilot, "d")
        assert app.search_input.value == "d"
        assert app.screen is app.screen.app.screen_stack[-1]
        assert not any(type(s).__name__ == "DeleteModal" for s in app.screen_stack)


async def test_tab_typed_into_the_box_does_not_switch_tabs(cfg):
    """tab is a priority binding, so unlike a letter it reaches the app even
    while an Input holds focus. Without the guard, typing would flip the view."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.showing_completed is False


async def test_tab_still_switches_tabs_when_the_box_is_closed(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.showing_completed is True


async def test_d_still_opens_the_delete_modal_when_the_box_is_closed(cfg):
    """The other half: guarding the binding must not disable it outright."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await pilot.press("d")
        await pilot.pause()
        assert any(type(s).__name__ == "DeleteModal" for s in app.screen_stack)


async def test_the_query_filters_the_completed_tab_too(cfg, state):
    log = state / "history.jsonl"
    for name in ("ubuntu.iso", "debian.iso", "clip.mp4"):
        history.append({"name": name, "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert len(app.completed.rows) == 3
        await type_query(pilot, "iso")
        assert sorted(r["name"] for r in app.completed.rows) == ["debian.iso", "ubuntu.iso"]


async def test_the_query_follows_you_across_tabs(cfg, state):
    log = state / "history.jsonl"
    history.append({"name": "ubuntu.iso", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    history.append({"name": "clip.mp4", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await type_query(pilot, "iso")
        await pilot.press("enter")
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert [r["name"] for r in app.completed.rows] == ["ubuntu.iso"]


async def test_the_completed_note_counts_without_a_denominator(cfg, state):
    """The log is unbounded, so "2 of N" would need a number nobody has."""
    log = state / "history.jsonl"
    history.append({"name": "ubuntu.iso", "status": "ok", "bytes": 1, "ts": 1, "path": ""}, log)
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        await type_query(pilot, "iso")
        assert "1 found" in app.search_note.text


async def test_the_search_box_is_not_in_the_dom_until_it_is_opened(cfg):
    """A hidden Input steals auto-focus and swallows the dashboard's keys."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.query_one("#body")
        assert not app.query(f"#{INPUT_ID}")
        assert not isinstance(app.focused, Input)


async def test_closing_the_box_takes_it_back_out_of_the_dom(cfg):
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("slash")
        await pilot.pause()
        assert app.query(f"#{INPUT_ID}")
        await pilot.press("escape")
        await pilot.pause()
        assert not app.query(f"#{INPUT_ID}")
        assert not isinstance(app.focused, Input)


async def test_a_filtered_row_is_the_one_acted_on(cfg):
    """Filtering rebuilds the row list, so the cursor must land on a match and
    not on whatever occupied that index before."""
    client = FakeClient()
    app = DlApp(cfg, client)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.refresh_data()
        await type_query(pilot, "mkv")
        await app.refresh_data()
        await pilot.press("enter")
        await pilot.pause()
        assert names(app) == ["b.mkv"]
        assert app.table.selected_gid == "g2"


async def test_modals_still_focus_their_input(cfg):
    """Turning auto-focus off app-wide to tame the search box would leave every
    modal's Input unfocused, and Enter would do nothing."""
    app = DlApp(cfg, FakeClient())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("a")
        await pilot.pause()
        assert isinstance(app.focused, Input | type(app.focused))
        assert app.focused is not None
