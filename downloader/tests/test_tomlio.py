import pytest

from dl import tomlio

SAMPLE = """\
[general]
theme = "aurora"

[proxy]
url = "http://127.0.0.1:2080"
# my note: needed here
domains = ["youtube.com"]
"""


def config(tmp_path, text=SAMPLE):
    path = tmp_path / "config.toml"
    path.write_text(text)
    return path


def test_setting_a_value_keeps_the_comment_beside_it(tmp_path):
    """The whole reason tomlkit is a dependency."""
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("proxy", "domains"), ["youtube.com", "github.com"])
    tomlio.write(path, doc)
    after = path.read_text()
    assert "# my note: needed here" in after
    assert "github.com" in after


def test_setting_a_value_keeps_key_order(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "ember")
    tomlio.write(path, doc)
    lines = [line for line in path.read_text().splitlines() if line.startswith("[")]
    assert lines == ["[general]", "[proxy]"]


def test_setting_a_value_changes_only_that_value(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "matrix")
    tomlio.write(path, doc)
    assert 'theme = "matrix"' in path.read_text()
    assert 'url = "http://127.0.0.1:2080"' in path.read_text()


def test_a_missing_section_is_created(tmp_path):
    path = config(tmp_path, '[general]\ntheme = "aurora"\n')
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("hooks", "on_complete"), "~/bin/x.sh")
    tomlio.write(path, doc)
    assert "[hooks]" in path.read_text()
    assert 'on_complete = "~/bin/x.sh"' in path.read_text()


def test_a_nested_table_is_created(tmp_path):
    path = config(tmp_path, '[general]\ntheme = "aurora"\n')
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("headers", "e.com", "Referer"), "https://e.com/")
    tomlio.write(path, doc)
    reread = tomlio.read(path)
    assert reread["headers"]["e.com"]["Referer"] == "https://e.com/"


def test_dropping_a_key_removes_it(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.drop(doc, ("proxy", "url"))
    tomlio.write(path, doc)
    assert "url" not in path.read_text()
    assert "domains" in path.read_text()


def test_dropping_a_key_that_is_not_there_is_quiet(tmp_path):
    path = config(tmp_path)
    doc = tomlio.read(path)
    tomlio.drop(doc, ("proxy", "nope"))
    tomlio.write(path, doc)


def test_a_syntax_error_is_reported_with_its_line(tmp_path):
    """config.load() silently falls back to defaults on a broken file. Saving
    those defaults over it would destroy the user's config."""
    path = config(tmp_path, '[general]\ntheme = "aurora\n')
    with pytest.raises(tomlio.BrokenConfig) as exc:
        tomlio.read(path)
    assert exc.value.line >= 1


def test_a_failed_write_leaves_the_original_untouched(tmp_path, monkeypatch):
    path = config(tmp_path)
    original = path.read_text()
    doc = tomlio.read(path)
    tomlio.set_value(doc, ("general", "theme"), "ember")

    def explode(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(tomlio.Path, "replace", explode)
    with pytest.raises(OSError):
        tomlio.write(path, doc)
    assert path.read_text() == original


def test_apply_writes_every_change_at_once(tmp_path):
    path = config(tmp_path)
    tomlio.apply(path, {("general", "theme"): "ember", ("limits", "splits"): 8})
    text = path.read_text()
    assert 'theme = "ember"' in text
    assert "splits = 8" in text
