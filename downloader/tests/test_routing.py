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


def proxied_cfg(*domains):
    return config.replace(config.defaults(), proxy_domains=tuple(domains))


def test_a_url_is_direct_when_no_domain_is_listed():
    assert routing.through_proxy("https://youtube.com/watch?v=x", config.defaults()) is False


def test_a_listed_host_goes_through_the_proxy():
    cfg = proxied_cfg("youtube.com")
    assert routing.through_proxy("https://youtube.com/watch?v=x", cfg) is True


def test_a_listed_host_covers_its_subdomains():
    """A blocked service is blocked at every hostname it answers on, so a proxy
    rule reads as the whole domain — unlike [domains], which routes one host."""
    cfg = proxied_cfg("youtube.com")
    assert routing.through_proxy("https://www.youtube.com/watch?v=x", cfg) is True


def test_a_listed_host_does_not_catch_a_lookalike():
    cfg = proxied_cfg("youtube.com")
    assert routing.through_proxy("https://notyoutube.com/x", cfg) is False


def test_a_star_prefix_matches_subdomains_only():
    cfg = proxied_cfg("*.googlevideo.com")
    assert routing.through_proxy("https://r5.googlevideo.com/x", cfg) is True
    assert routing.through_proxy("https://googlevideo.com/x", cfg) is False


def test_matching_ignores_case_and_port():
    cfg = proxied_cfg("YouTube.com")
    assert routing.through_proxy("https://WWW.YOUTUBE.COM:443/x", cfg) is True


def test_the_p_flag_forces_a_url_that_matches_nothing():
    assert routing.through_proxy("https://example.com/a.iso", config.defaults(), forced=True) is True


def test_a_url_without_a_host_is_direct():
    assert routing.through_proxy("magnet:?xt=urn:btih:abc", proxied_cfg("youtube.com")) is False


def header_cfg(rules):
    return config.replace(config.defaults(), headers=rules)


def test_no_headers_configured_sends_none():
    assert routing.headers_for("https://e.com/a.iso", config.defaults()) == {}


def test_headers_are_matched_by_host():
    cfg = header_cfg({"dl6.indllserver.info": {"Referer": "https://indllserver.info/"}})
    got = routing.headers_for("https://dl6.indllserver.info/a.mkv", cfg)
    assert got == {"Referer": "https://indllserver.info/"}


def test_headers_follow_the_same_host_rule_as_the_proxy():
    """A bare name covers subdomains, so one rule serves dl6, dl7 and the rest."""
    cfg = header_cfg({"indllserver.info": {"Referer": "https://indllserver.info/"}})
    assert routing.headers_for("https://dl6.indllserver.info/a.mkv", cfg)
    assert routing.headers_for("https://indllserver.info/a.mkv", cfg)
    assert routing.headers_for("https://notindllserver.info/a.mkv", cfg) == {}


def test_a_star_prefix_still_means_subdomains_only():
    cfg = header_cfg({"*.cdn.io": {"Referer": "x"}})
    assert routing.headers_for("https://a.cdn.io/f", cfg)
    assert routing.headers_for("https://cdn.io/f", cfg) == {}


def test_headers_from_every_matching_rule_are_merged():
    cfg = header_cfg({
        "example.com": {"Referer": "https://example.com/"},
        "dl.example.com": {"X-Token": "abc"},
    })
    got = routing.headers_for("https://dl.example.com/a.iso", cfg)
    assert got == {"Referer": "https://example.com/", "X-Token": "abc"}


def test_the_more_specific_rule_wins_a_clash():
    cfg = header_cfg({
        "example.com": {"Referer": "general"},
        "dl.example.com": {"Referer": "specific"},
    })
    assert routing.headers_for("https://dl.example.com/a", cfg)["Referer"] == "specific"


def test_a_url_without_a_host_gets_no_headers():
    cfg = header_cfg({"example.com": {"Referer": "x"}})
    assert routing.headers_for("magnet:?xt=urn:btih:abc", cfg) == {}


def test_header_lines_are_formatted_the_way_aria2_wants():
    assert routing.header_lines({"Referer": "https://x/"}) == ["Referer: https://x/"]


def test_header_lines_of_nothing_is_empty():
    assert routing.header_lines({}) == []
