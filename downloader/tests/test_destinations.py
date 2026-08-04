from pathlib import Path

import pytest

from dl import config
from dl.destinations import (
    Candidate,
    candidates,
    create_candidate,
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


@pytest.mark.parametrize("text", ["/tmp/x", "~/stuff", "./here", "."])
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
