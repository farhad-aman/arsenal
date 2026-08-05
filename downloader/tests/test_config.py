import pytest

from dl import config


@pytest.mark.parametrize(
    "text,seconds", [("30s", 30), ("10m", 600), ("2h", 7200), ("45", 45), ("0", 0)]
)
def test_parse_duration(text, seconds):
    assert config.parse_duration(text) == seconds


def test_parse_duration_rejects_garbage():
    with pytest.raises(ValueError):
        config.parse_duration("soon")


def test_defaults_point_the_proxy_at_singbox():
    assert config.defaults().proxy == "http://127.0.0.1:2080"


def test_proxy_url_can_be_overridden(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[proxy]\nurl = "http://127.0.0.1:8080"\n')
    assert config.load(target).proxy == "http://127.0.0.1:8080"


def test_proxy_falls_back_when_the_section_is_absent(tmp_path):
    target = tmp_path / "config.toml"
    target.write_text('[general]\nmax_concurrent = 2\n')
    assert config.load(target).proxy == "http://127.0.0.1:2080"


def test_default_toml_documents_the_proxy(tmp_path):
    target = tmp_path / "config.toml"
    config.write_default(target)
    assert config.load(target).proxy == "http://127.0.0.1:2080"


@pytest.mark.parametrize(
    "text,rate", [("off", "0"), ("OFF", "0"), ("", "0"), ("2M", "2M"), ("500K", "500K")]
)
def test_parse_rate(text, rate):
    assert config.parse_rate(text) == rate


def test_load_missing_file_returns_defaults(tmp_path):
    cfg = config.load(tmp_path / "nope.toml")
    assert cfg.general.max_concurrent == 3
    assert cfg.general.theme == "aurora"
    assert cfg.general.idle_timeout == 600
    assert cfg.limits.connections == 16
    assert "video" in cfg.categories


def test_default_categories_all_have_icon_and_hue():
    for cat in config.DEFAULT_CATEGORIES.values():
        assert cat.icon
        assert cat.hue.startswith("#")
        assert cat.ext


def test_partial_file_merges_over_defaults(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = 9\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 9
    assert cfg.general.theme == "aurora"
    assert cfg.limits.connections == 16


def test_user_category_replaces_default_of_same_name(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[categories.video]\ndir = "~/Vids"\next = ["mkv"]\nicon = "V"\nhue = "#111111"\n'
    )
    cfg = config.load(p)
    assert cfg.categories["video"].dir.name == "Vids"
    assert cfg.categories["video"].ext == ("mkv",)
    assert "iso" in cfg.categories


def test_extensions_are_lowercased_and_stripped_of_dots(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '[categories.video]\ndir = "~/V"\next = [".MKV", "Mp4"]\nicon = "V"\nhue = "#111111"\n'
    )
    cfg = config.load(p)
    assert cfg.categories["video"].ext == ("mkv", "mp4")


def test_unknown_keys_are_ignored(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = 4\nwarp_drive = true\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 4


def test_malformed_toml_falls_back_to_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[general\nmax_concurrent = ')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 3
    assert "config.toml" in capsys.readouterr().err


def test_bad_value_type_falls_back_to_defaults(tmp_path, capsys):
    p = tmp_path / "config.toml"
    p.write_text('[general]\nmax_concurrent = "lots"\n')
    cfg = config.load(p)
    assert cfg.general.max_concurrent == 3
    assert capsys.readouterr().err


def test_write_default_then_load_roundtrips(tmp_path):
    p = tmp_path / "config.toml"
    config.write_default(p)
    cfg = config.load(p)
    assert cfg.general.theme == "aurora"
    assert cfg.categories["iso"].ext
    assert cfg.domains["huggingface.co"] == "models"


def test_paths_are_expanded(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[general]\ndefault_dir = "~/Elsewhere"\n')
    cfg = config.load(p)
    assert cfg.general.default_dir.is_absolute()
    assert "~" not in str(cfg.general.default_dir)


def test_proxy_domains_default_to_none(tmp_path):
    assert config.defaults().proxy_domains == ()


def test_proxy_domains_are_read_from_the_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[proxy]\nurl = "http://127.0.0.1:9"\ndomains = ["youtube.com", "*.x.io"]\n')
    cfg = config.load(path)
    assert cfg.proxy == "http://127.0.0.1:9"
    assert cfg.proxy_domains == ("youtube.com", "*.x.io")


def test_proxy_domains_are_lowercased(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[proxy]\ndomains = ["YouTube.COM"]\n')
    assert config.load(path).proxy_domains == ("youtube.com",)


def test_a_config_without_a_proxy_section_still_loads(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[general]\ntheme = "mono"\n')
    assert config.load(path).proxy_domains == ()


def test_the_written_default_config_documents_proxy_domains(tmp_path):
    path = tmp_path / "config.toml"
    config.write_default(path)
    assert "domains" in path.read_text().split("[proxy]", 1)[1].split("[", 1)[0]
    assert config.load(path).proxy_domains == ()
