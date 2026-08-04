from pathlib import Path

import pytest

from dl import config, routing


@pytest.fixture
def cfg():
    return config.defaults()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://e.com/ubuntu.iso", "ubuntu.iso"),
        ("https://e.com/a/b/c/file.tar.gz", "file.tar.gz"),
        ("https://e.com/file.zip?token=abc&x=1", "file.zip"),
        ("https://e.com/file.zip#frag", "file.zip"),
        ("https://e.com/My%20Movie.mkv", "My Movie.mkv"),
        ("https://e.com/", ""),
        ("https://e.com", ""),
        ("magnet:?xt=urn:btih:abc", ""),
    ],
)
def test_filename_from_url(url, expected):
    assert routing.filename_from_url(url) == expected


@pytest.mark.parametrize(
    "name,category",
    [
        ("ubuntu.iso", "iso"),
        ("show.mkv", "video"),
        ("SHOW.MKV", "video"),
        ("song.flac", "audio"),
        ("paper.pdf", "docs"),
        ("tool.pkg", "apps"),
        ("model.safetensors", "models"),
        ("data.tar.gz", "archive"),
    ],
)
def test_extension_routing(name, category, cfg):
    r = routing.resolve(f"https://e.com/{name}", name, cfg)
    assert r.category.name == category
    assert r.path == cfg.categories[category].dir


def test_unknown_extension_falls_back(cfg):
    r = routing.resolve("https://e.com/thing.qqq", "thing.qqq", cfg)
    assert r.category.name == "other"
    assert r.path == cfg.general.default_dir


def test_no_extension_falls_back(cfg):
    r = routing.resolve("https://e.com/README", "README", cfg)
    assert r.category.name == "other"


def test_empty_filename_falls_back(cfg):
    r = routing.resolve("https://e.com/", "", cfg)
    assert r.category.name == "other"


def test_domain_exact_match_beats_extension(cfg):
    r = routing.resolve("https://huggingface.co/x/model.zip", "model.zip", cfg)
    assert r.category.name == "models"


def test_domain_wildcard_matches_subdomain(cfg):
    r = routing.resolve("https://api.github.com/x/thing.zip", "thing.zip", cfg)
    assert r.category.name == "code"


def test_domain_wildcard_does_not_match_apex(cfg):
    r = routing.resolve("https://github.com/x/thing.zip", "thing.zip", cfg)
    assert r.category.name == "archive"


def test_domain_match_is_case_insensitive(cfg):
    r = routing.resolve("https://HuggingFace.CO/x/f.zip", "f.zip", cfg)
    assert r.category.name == "models"


def test_domain_match_ignores_port(cfg):
    r = routing.resolve("https://huggingface.co:8443/x/f.zip", "f.zip", cfg)
    assert r.category.name == "models"


def test_domain_pointing_at_unknown_category_falls_back(cfg):
    broken = config.Config(cfg.general, cfg.limits, cfg.categories, {"e.com": "nope"})
    r = routing.resolve("https://e.com/f.zip", "f.zip", broken)
    assert r.category.name == "archive"


def test_explicit_dir_wins_over_everything(cfg):
    r = routing.resolve("https://huggingface.co/m.iso", "m.iso", cfg, explicit_dir=Path("/tmp/x"))
    assert r.path == Path("/tmp/x")
    assert r.category.name == "other"


def test_resolve_is_pure_and_creates_nothing(cfg, tmp_path):
    target = tmp_path / "never"
    routing.resolve("https://e.com/a.iso", "a.iso", cfg, explicit_dir=target)
    assert not target.exists()
