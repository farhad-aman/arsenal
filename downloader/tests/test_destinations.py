from pathlib import Path

import pytest

from dl import config
from dl.destinations import (
    Candidate,
    candidates,
    create_candidate,
    disk_candidates,
    ensure_writable,
    filter_candidates,
    recent_destinations,
)


@pytest.fixture
def cfg():
    return config.defaults()


def rec(path):
    return {"path": path, "name": Path(path).name, "status": "ok"}


def test_recent_destinations_of_empty_history():
    assert recent_destinations([]) == []


def test_recent_destinations_counts_parent_directories():
    got = recent_destinations([rec("/a/x.mkv"), rec("/a/y.mkv"), rec("/b/z.iso")])
    assert got[0] == (Path("/a"), 2)
    assert got[1] == (Path("/b"), 1)


def test_recent_destinations_breaks_ties_by_most_recent():
    got = recent_destinations([rec("/old/a.mkv"), rec("/new/b.mkv")])
    assert [p for p, _ in got] == [Path("/new"), Path("/old")]


def test_recent_destinations_skips_records_without_a_path():
    got = recent_destinations([{"name": "x", "status": "error"}, rec("/a/x.mkv")])
    assert got == [(Path("/a"), 1)]


def test_recent_destinations_respects_the_limit():
    records = [rec(f"/d{i}/f.mkv") for i in range(10)]
    assert len(recent_destinations(records, limit=3)) == 3


def test_candidates_put_the_routed_default_first(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert items[0].path == Path("/movies")
    assert items[0].kind == "default"
    assert items[0].icon == "🎬"
    assert "mkv" in items[0].note


def test_candidates_note_for_an_uncategorised_file(cfg):
    from dl.routing import OTHER

    items = candidates("README", Path("/downloads"), OTHER, cfg, [], Path("/cwd"))
    assert items[0].note == "default folder"


def test_candidates_include_recents_after_the_default(cfg):
    records = [rec("/series/a.mkv"), rec("/series/b.mkv")]
    items = candidates(
        "movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/cwd")
    )
    assert items[1].path == Path("/series")
    assert items[1].kind == "recent"
    assert items[1].note == "used 2×"


def test_candidates_deduplicate_a_recent_that_is_the_default(cfg):
    records = [rec("/movies/a.mkv")]
    items = candidates(
        "movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/cwd")
    )
    assert [c.path for c in items].count(Path("/movies")) == 1


def test_candidates_include_other_categories(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    kinds = [c.kind for c in items]
    assert "category" in kinds
    assert cfg.categories["iso"].dir in [c.path for c in items]


def test_candidates_end_with_the_current_directory(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert items[-1].path == Path("/cwd")
    assert items[-1].kind == "cwd"
    assert items[-1].note == "current dir"


def test_candidates_never_repeat_a_path(cfg):
    records = [rec("/movies/a.mkv"), rec(str(cfg.categories["iso"].dir / "b.iso"))]
    items = candidates(
        "movie.mkv", Path("/movies"), cfg.categories["video"], cfg, records, Path("/movies")
    )
    paths = [c.path for c in items]
    assert len(paths) == len(set(paths))


@pytest.mark.parametrize("text", ["/tmp/x", "~/stuff", "./here"])
def test_create_candidate_for_pathlike_text(text):
    made = create_candidate(text)
    assert made is not None
    assert made.kind == "create"
    assert made.note == "create"


@pytest.mark.parametrize("text", ["", "movies", "ser"])
def test_create_candidate_rejects_non_paths(text):
    assert create_candidate(text) is None


def test_create_candidate_expands_home():
    made = create_candidate("~/stuff")
    assert "~" not in str(made.path)


def test_create_candidate_survives_an_unknown_user(tmp_path):
    """Path("~s").expanduser() raises rather than returning the text unchanged."""
    assert create_candidate("~s") is None


def test_create_candidate_is_none_for_a_directory_that_already_exists(tmp_path):
    assert create_candidate(str(tmp_path)) is None


def test_disk_candidates_list_real_subdirectories(tmp_path):
    (tmp_path / "projects").mkdir()
    (tmp_path / "pictures").mkdir()
    found = disk_candidates(f"{tmp_path}/pro")
    assert [c.path for c in found] == [tmp_path / "projects"]
    assert found[0].kind == "disk"


def test_disk_candidates_skip_files(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    assert disk_candidates(f"{tmp_path}/notes") == []


def test_disk_candidates_list_everything_under_a_trailing_slash(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert [c.path for c in disk_candidates(f"{tmp_path}/")] == [tmp_path / "a", tmp_path / "b"]


def test_disk_candidates_are_case_insensitive(tmp_path):
    (tmp_path / "Projects").mkdir()
    assert [c.path for c in disk_candidates(f"{tmp_path}/proj")] == [tmp_path / "Projects"]


def test_disk_candidates_hide_dotfiles_until_asked_for(tmp_path):
    (tmp_path / ".config").mkdir()
    assert disk_candidates(f"{tmp_path}/") == []
    assert [c.path for c in disk_candidates(f"{tmp_path}/.co")] == [tmp_path / ".config"]


def test_disk_candidates_expand_home():
    found = disk_candidates("~/")
    assert found
    assert all(c.path.parent == Path.home() for c in found)


@pytest.mark.parametrize("text", ["", "movies", "ser"])
def test_disk_candidates_ignore_non_paths(text):
    assert disk_candidates(text) == []


def test_disk_candidates_ignore_an_unknown_user(tmp_path):
    assert disk_candidates("~nobody-like-this/") == []


def test_disk_candidates_ignore_a_parent_that_does_not_exist(tmp_path):
    assert disk_candidates(f"{tmp_path}/missing/deeper") == []


def test_disk_candidates_respect_the_limit(tmp_path):
    for i in range(10):
        (tmp_path / f"d{i}").mkdir()
    assert len(disk_candidates(f"{tmp_path}/", limit=3)) == 3


def test_filter_returns_everything_for_empty_text(cfg):
    items = candidates("movie.mkv", Path("/movies"), cfg.categories["video"], cfg, [], Path("/cwd"))
    assert filter_candidates("", items) == items


def test_filter_matches_a_subsequence():
    home = Path.home()
    items = [
        Candidate(home / "Movies/Series", "🕘", "used 2×", "recent"),
        Candidate(home / "Downloads/ISO", "💿", "category", "category"),
    ]
    assert [c.path for c in filter_candidates("ser", items)] == [home / "Movies/Series"]


def test_filter_ignores_the_home_prefix():
    """Every macOS path contains "/Users/", so matching the raw path would make
    "ser" match everything."""
    home = Path.home()
    items = [Candidate(home / "Downloads/ISO", "💿", "category", "category")]
    assert filter_candidates("ser", items) == []
    assert filter_candidates("users", items) == []


def test_filter_is_case_insensitive():
    items = [Candidate(Path.home() / "Movies", "🎬", "x", "recent")]
    assert filter_candidates("MOVIES", items)


def test_filter_returns_empty_when_nothing_matches():
    items = [Candidate(Path("/a"), "🎬", "x", "recent")]
    assert filter_candidates("zzzz", items) == []


def test_ensure_writable_creates_the_directory(tmp_path):
    target = tmp_path / "deep" / "new"
    assert ensure_writable(target) is True
    assert target.is_dir()


def test_ensure_writable_is_false_for_an_uncreatable_path(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        assert ensure_writable(locked / "sub") is False
    finally:
        locked.chmod(0o700)
