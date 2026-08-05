import pytest

from dl import ytrun
from dl.tui.ytflow import YouTubeSetupApp
from dl.youtube import DEFAULTS


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def app_with(cfg, monkeypatch, probe, tmp_path):
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None: spawned.append(job))
    monkeypatch.setattr("dl.tui.ytflow._probe_job", probe)
    app = YouTubeSetupApp(cfg, ["https://youtu.be/abc"])
    return app, spawned


def job_for(cfg, tmp_path):
    from dl import ytjob

    return ytjob.new_job("https://youtu.be/abc", tmp_path / "out", DEFAULTS)


async def failing_probe(job, timeout=None):
    raise ytrun.ProbeFailed("timed out after 180s")


async def test_a_probe_it_cannot_complete_asks_before_queuing(cfg, tmp_path, monkeypatch):
    """An unanswered probe used to look identical to 'no collision', so the
    download went out with the duplicate check quietly skipped."""
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if len(app.screen_stack) > 1 and "could not" in app.screen.question.lower():
                break
        assert "could not" in app.screen.question.lower()
        assert spawned == []


async def test_answering_yes_queues_it_anyway(cfg, tmp_path, monkeypatch):
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if len(app.screen_stack) > 1 and hasattr(app.screen, "question"):
                break
        await pilot.click("#yes")
        for _ in range(10):
            await pilot.pause()
            if spawned:
                break
    assert spawned and spawned[0]["url"] == "https://youtu.be/abc"


async def test_answering_no_skips_it(cfg, tmp_path, monkeypatch):
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if len(app.screen_stack) > 1 and hasattr(app.screen, "question"):
                break
        await pilot.click("#no")
        for _ in range(10):
            await pilot.pause()
            if app.skipped:
                break
    assert spawned == []
    assert app.skipped


async def test_a_probe_that_answers_still_queues_without_asking(cfg, tmp_path, monkeypatch):
    async def answers(job, timeout=None):
        return "A Clip", str(tmp_path / "A Clip.mp4"), 4242

    app, spawned = app_with(cfg, monkeypatch, answers, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if spawned:
                break
    assert spawned and spawned[0]["title"] == "A Clip"
    assert spawned[0]["total"] == 4242
