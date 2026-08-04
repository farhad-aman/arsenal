import asyncio
import inspect

import pytest

from dl import config


def pytest_pyfunc_call(pyfuncitem):
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    kwargs = {
        name: pyfuncitem.funcargs[name]
        for name in pyfuncitem._fixtureinfo.argnames
        if name in pyfuncitem.funcargs
    }
    asyncio.run(pyfuncitem.obj(**kwargs))
    return True


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Point the state directory at tmp_path for every test.

    The dashboard reads yt-dlp job files straight off disk, so without this a
    test run sees the real ~/.local/state/dl and whatever is queued there.
    """
    from dl.tui import app as app_module

    monkeypatch.setattr(app_module, "STATE_DIR", tmp_path / "state", raising=False)


@pytest.fixture
def sandbox_cfg(tmp_path):
    """Defaults with every destination redirected under tmp_path.

    Anything that calls mkdir on a resolved path must use this, so a test run
    never creates directories in the real home.
    """
    base = tmp_path / "dest"
    base_defaults = config.defaults()
    cats = {
        name: config.Category(name, base / name, cat.ext, cat.icon, cat.hue)
        for name, cat in base_defaults.categories.items()
    }
    general = config.replace(base_defaults.general, default_dir=base / "other")
    return config.Config(general, base_defaults.limits, cats, dict(base_defaults.domains))
