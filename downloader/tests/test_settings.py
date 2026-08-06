import dataclasses

import pytest

from dl import config, settings


def field(**over):
    base = dict(path=("limits", "splits"), label="Splits", kind="int")
    base.update(over)
    return settings.Field(**base)


def test_every_field_accepts_its_own_current_value(sandbox_cfg):
    """A schema that rejects the running config would block every save.

    sandbox_cfg, not defaults(): validating a path field creates the folder,
    and defaults() points at the real ~/Downloads.
    """
    for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS):
        for entry in section:
            shown = settings.render(settings.current(sandbox_cfg, entry))
            settings.parse(entry, shown)


def test_a_field_that_may_be_empty_accepts_empty():
    """No hook and no cookie browser are both legitimate settings."""
    for entry in settings.HOOKS + settings.YOUTUBE:
        if entry.allow_empty:
            assert settings.parse(entry, "") == ""


def test_the_two_optional_fields_are_the_only_ones_that_may_be_empty():
    optional = [
        f.path
        for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS)
        for f in section
        if f.allow_empty
    ]
    assert sorted(optional) == [("hooks", "on_complete"), ("youtube", "cookies_from")]


def test_an_int_field_rejects_words():
    with pytest.raises(settings.Invalid) as exc:
        settings.parse(field(), "banana")
    assert "number" in str(exc.value).lower()


def test_an_int_field_rejects_zero_and_below():
    with pytest.raises(settings.Invalid):
        settings.parse(field(), "0")


def test_an_int_field_accepts_a_positive_number():
    assert settings.parse(field(), "12") == 12


def test_a_choice_field_rejects_something_not_offered():
    entry = field(kind="choice", choices=("aurora", "ember"))
    with pytest.raises(settings.Invalid) as exc:
        settings.parse(entry, "neon")
    assert "aurora" in str(exc.value)


def test_a_choice_field_accepts_an_offered_value():
    entry = field(kind="choice", choices=("aurora", "ember"))
    assert settings.parse(entry, "ember") == "ember"


@pytest.mark.parametrize("raw", ["500K", "2M", "1G", "off", "1024"])
def test_a_rate_field_accepts_the_spellings_aria2_takes(raw):
    assert settings.parse(field(kind="rate"), raw)


@pytest.mark.parametrize("raw", ["fast", "500KB", "-1", ""])
def test_a_rate_field_rejects_anything_else(raw):
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="rate"), raw)


def test_a_duration_field_uses_the_same_parser_as_the_config():
    assert settings.parse(field(kind="duration"), "10m") == "10m"


def test_a_duration_field_rejects_nonsense():
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="duration"), "soon")


def test_a_bool_field_reads_on_and_off():
    entry = field(kind="bool")
    assert settings.parse(entry, "on") is True
    assert settings.parse(entry, "off") is False


def test_a_path_field_expands_home_and_returns_text(tmp_path):
    entry = field(kind="path")
    got = settings.parse(entry, str(tmp_path / "somewhere"))
    assert str(tmp_path) in got


def test_a_path_field_rejects_somewhere_it_cannot_write(tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(settings.Invalid):
            settings.parse(field(kind="path"), str(locked / "sub"))
    finally:
        locked.chmod(0o700)


def test_a_colour_field_wants_a_hex_value():
    assert settings.parse(field(kind="colour"), "#c678dd") == "#c678dd"
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="colour"), "purple")


def test_a_text_field_takes_anything_but_empty():
    assert settings.parse(field(kind="text"), " hello ") == "hello"
    with pytest.raises(settings.Invalid):
        settings.parse(field(kind="text"), "   ")


def test_bools_render_as_on_and_off():
    assert settings.render(True) == "on"
    assert settings.render(False) == "off"


def test_theme_is_the_only_live_field():
    live = [
        f.path
        for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS)
        for f in section
        if f.live
    ]
    assert sorted(live) == [("general", "theme")]


def test_every_config_setting_is_reachable_from_the_screen():
    """This config grew from 12 keys to 20 in a day. Without this the schema
    falls behind and a new setting is silently uneditable."""
    holders = (config.Config, config.General, config.Limits)
    container_types = holders + tuple(h.__name__ for h in holders)
    for holder in holders:
        for entry in dataclasses.fields(holder):
            if entry.type in container_types:
                continue  # a container of settings, not a setting
            assert entry.name in settings.EDITABLE, (
                f"{holder.__name__}.{entry.name} is not editable — add a Field for it, "
                f"or name it in settings.EDITABLE"
            )


def test_nothing_is_claimed_editable_that_the_config_does_not_have():
    """The other direction: a stale name in EDITABLE would hide real drift."""
    holders = (config.Config, config.General, config.Limits)
    actual = {entry.name for holder in holders for entry in dataclasses.fields(holder)}
    assert settings.EDITABLE <= actual, settings.EDITABLE - actual


def test_every_schema_field_can_be_read_from_a_config(sandbox_cfg):
    for section in (settings.GENERAL, settings.LIMITS, settings.YOUTUBE, settings.HOOKS):
        for entry in section:
            settings.current(sandbox_cfg, entry)
