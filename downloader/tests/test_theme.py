import pytest

from dl import config, theme


@pytest.fixture
def cfg():
    return config.defaults()


def test_every_theme_is_present():
    assert set(theme.THEMES) == {"aurora", "ember", "matrix", "dusk", "mono"}


def test_mono_is_the_only_theme_without_colour_or_icons():
    for name, t in theme.THEMES.items():
        assert t.mono is (name == "mono"), name
        assert t.icons is (name != "mono"), name


def test_every_theme_has_a_non_empty_ramp():
    for t in theme.THEMES.values():
        assert len(t.ramp) >= 2
        assert all(c.startswith("#") for c in t.ramp)


def test_select_uses_config_theme(cfg):
    assert theme.select(cfg, env={}).name == "aurora"


def test_select_falls_back_for_unknown_name(cfg):
    broken = config.Config(
        config.replace(cfg.general, theme="neon"), cfg.limits, cfg.categories, cfg.domains
    )
    assert theme.select(broken, env={}).name == "aurora"


def test_no_color_forces_mono(cfg):
    assert theme.select(cfg, env={"NO_COLOR": "1"}).name == "mono"


def test_dumb_term_forces_mono(cfg):
    assert theme.select(cfg, env={"TERM": "dumb"}).name == "mono"


def test_mono_theme_is_marked_mono():
    assert theme.THEMES["mono"].mono is True
    assert theme.THEMES["aurora"].mono is False


def test_icon_for_emoji_theme_uses_category_icon(cfg):
    t = theme.THEMES["aurora"]
    assert theme.icon_for(cfg.categories["iso"], t) == "💿"


def test_icon_for_mono_theme_uses_ascii_tag(cfg):
    t = theme.THEMES["mono"]
    tag = theme.icon_for(cfg.categories["iso"], t)
    assert tag == "IS"
    assert len(tag) == 2


def test_mono_is_the_only_way_to_turn_emoji_off(cfg):
    """Emoji are the default everywhere; mono is the one opt-out."""
    for name in theme.THEMES:
        chosen = config.Config(
            config.replace(cfg.general, theme=name), cfg.limits, cfg.categories, cfg.domains
        )
        assert theme.select(chosen, env={}).icons is (name != "mono"), name


def test_ramp_color_endpoints(cfg):
    t = theme.THEMES["aurora"]
    assert theme.ramp_color(t, 0.0) == t.ramp[0]
    assert theme.ramp_color(t, 1.0) == t.ramp[-1]


def test_ramp_color_clamps(cfg):
    t = theme.THEMES["aurora"]
    assert theme.ramp_color(t, -5) == t.ramp[0]
    assert theme.ramp_color(t, 5) == t.ramp[-1]
