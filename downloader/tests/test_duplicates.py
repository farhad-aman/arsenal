from pathlib import Path

from dl import duplicates
from dl.duplicates import BOTH, DOWNLOAD, OVERWRITE, PATH_ONLY, RENAME, SKIP, URL_ONLY


def rec(path, url, status="ok", **over):
    row = {"path": str(path), "url": url, "status": status, "bytes": 10, "name": Path(path).name}
    row.update(over)
    return row


def dl(gid, path, url, status="active"):
    return {
        "gid": gid,
        "status": status,
        "totalLength": "100",
        "files": [{"path": str(path), "uris": [{"uri": url}]}],
    }


URL = "https://e.com/movie.mkv"
OTHER_URL = "https://mirror.example/movie.mkv"


def test_no_collision_on_a_clean_target(tmp_path):
    assert duplicates.detect(URL, tmp_path / "movie.mkv", [], []) is None


def test_same_url_and_path_on_disk_is_a_both_collision(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_text("old")
    found = duplicates.detect(URL, target, [rec(target, URL)], [])
    assert found.kind == BOTH
    assert found.path == target
    assert found.in_flight is False


def test_a_different_url_at_the_same_path_is_a_path_collision(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_text("old")
    found = duplicates.detect(URL, target, [rec(target, OTHER_URL)], [])
    assert found.kind == PATH_ONLY


def test_an_unknown_file_on_disk_is_treated_as_a_different_file(tmp_path):
    """No history row means we cannot claim it is the same download."""
    target = tmp_path / "movie.mkv"
    target.write_text("mystery")
    assert duplicates.detect(URL, target, [], []).kind == PATH_ONLY


def test_same_url_elsewhere_is_a_url_collision(tmp_path):
    previous = tmp_path / "old-folder" / "movie.mkv"
    previous.parent.mkdir()
    previous.write_text("done")
    found = duplicates.detect(URL, tmp_path / "new" / "movie.mkv", [rec(previous, URL)], [])
    assert found.kind == URL_ONLY
    assert found.path == previous


def test_a_url_whose_file_is_gone_is_not_a_collision(tmp_path):
    previous = tmp_path / "deleted.mkv"
    assert duplicates.detect(URL, tmp_path / "new.mkv", [rec(previous, URL)], []) is None


def test_a_failed_past_attempt_is_not_a_collision(tmp_path):
    previous = tmp_path / "movie.mkv"
    previous.write_text("partial")
    found = duplicates.detect(URL, tmp_path / "elsewhere" / "movie.mkv",
                              [rec(previous, URL, status="error")], [])
    assert found is None


def test_an_unfinished_download_at_the_same_path_collides(tmp_path):
    target = tmp_path / "movie.mkv"
    found = duplicates.detect(URL, target, [], [dl("g1", target, URL)])
    assert found.kind == BOTH
    assert found.in_flight is True
    assert found.gid == "g1"
    assert found.status == "active"


def test_an_unfinished_download_of_another_url_at_that_path_is_path_only(tmp_path):
    target = tmp_path / "movie.mkv"
    found = duplicates.detect(URL, target, [], [dl("g1", target, OTHER_URL)])
    assert found.kind == PATH_ONLY
    assert found.gid == "g1"


def test_the_same_url_downloading_to_another_folder_is_url_only(tmp_path):
    running = tmp_path / "a" / "movie.mkv"
    found = duplicates.detect(URL, tmp_path / "b" / "movie.mkv", [], [dl("g1", running, URL)])
    assert found.kind == URL_ONLY
    assert found.gid == "g1"


def test_an_in_flight_duplicate_wins_over_a_history_row(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_text("stale")
    found = duplicates.detect(URL, target, [rec(target, URL)], [dl("g7", target, URL)])
    assert found.gid == "g7"
    assert found.in_flight is True


def test_an_unnamed_target_falls_back_to_url_matching(tmp_path):
    """Until the server names the file there is no path to compare."""
    previous = tmp_path / "movie.mkv"
    previous.write_text("done")
    assert duplicates.detect(URL, None, [rec(previous, URL)], []).kind == URL_ONLY
    assert duplicates.detect(OTHER_URL, None, [rec(previous, URL)], []) is None


def test_an_unnamed_target_does_not_match_a_directory(tmp_path):
    assert duplicates.detect(URL, None, [], []) is None


def test_detect_target_ignores_where_the_file_came_from(tmp_path):
    """The same video at another resolution shares a URL but is a different
    file, so YouTube compares destinations only."""
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"x" * 64)
    found = duplicates.detect_target(target)
    assert found.kind == duplicates.TARGET
    assert found.choices == (SKIP, RENAME, OVERWRITE)
    assert found.size == 64
    assert found.risky_overwrite is False


def test_detect_target_of_a_free_path_is_none(tmp_path):
    assert duplicates.detect_target(tmp_path / "clip.mp4") is None


def test_detect_target_does_not_treat_a_directory_as_a_file(tmp_path):
    assert duplicates.detect_target(tmp_path) is None


def test_free_name_leaves_an_unused_path_alone(tmp_path):
    target = tmp_path / "clip.mp4"
    assert duplicates.free_name(target) == target


def test_free_name_steps_past_what_is_taken(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"a")
    assert duplicates.free_name(tmp_path / "clip.mp4").name == "clip (2).mp4"


def test_free_name_keeps_counting(tmp_path):
    for name in ("clip.mp4", "clip (2).mp4", "clip (3).mp4"):
        (tmp_path / name).write_bytes(b"a")
    assert duplicates.free_name(tmp_path / "clip.mp4").name == "clip (4).mp4"


def test_choices_offered_for_each_kind():
    assert duplicates.choices_for(BOTH) == (SKIP, RENAME, OVERWRITE)
    assert duplicates.choices_for(URL_ONLY) == (SKIP, DOWNLOAD)
    assert duplicates.choices_for(PATH_ONLY) == (SKIP, RENAME, OVERWRITE)


def test_only_a_path_collision_warns_that_the_file_differs(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_text("x")
    path_only = duplicates.detect(URL, target, [rec(target, OTHER_URL)], [])
    both = duplicates.detect(URL, target, [rec(target, URL)], [])
    assert path_only.risky_overwrite is True
    assert both.risky_overwrite is False


def test_collision_reports_the_size_of_what_is_already_there(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_bytes(b"x" * 2048)
    assert duplicates.detect(URL, target, [rec(target, URL)], []).size == 2048
