import pytest

from dl import ytrun
from dl.tui.ytflow import YouTubeSetupApp
from dl.youtube import DEFAULTS


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


def app_with(cfg, monkeypatch, probe, tmp_path):
    """The chain lives in YouTubeAdder now; the app is one of its two hosts."""
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None: spawned.append(job))
    monkeypatch.setattr("dl.ytrun.probe", probe)
    app = YouTubeSetupApp(cfg, ["https://youtu.be/abc"])
    app.adder._spawn = lambda job, state=None: spawned.append(job)
    return app, spawned


def job_for(cfg, tmp_path):
    from dl import ytjob

    return ytjob.new_job("https://youtu.be/abc", tmp_path / "out", DEFAULTS)


def failing_probe(job, timeout=None):
    raise ytrun.ProbeFailed("timed out after 180s")


async def test_a_probe_it_cannot_complete_asks_before_queuing(cfg, tmp_path, monkeypatch):
    """An unanswered probe used to look identical to 'no collision', so the
    download went out with the duplicate check quietly skipped."""
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app.adder._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if len(app.screen_stack) > 1 and "could not" in getattr(
                app.screen, "question", ""
            ).lower():
                break
        assert "could not" in app.screen.question.lower()
        assert spawned == []


async def test_answering_yes_queues_it_anyway(cfg, tmp_path, monkeypatch):
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app.adder._settle(0, job_for(cfg, tmp_path)), exclusive=False)
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
        app.run_worker(app.adder._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if len(app.screen_stack) > 1 and hasattr(app.screen, "question"):
                break
        await pilot.click("#no")
        for _ in range(10):
            await pilot.pause()
            if app.adder.skipped:
                break
    assert spawned == []
    assert app.adder.skipped


async def test_a_probe_that_answers_still_queues_without_asking(cfg, tmp_path, monkeypatch):
    def answers(job, timeout=None):
        return "A Clip", str(tmp_path / "A Clip.mp4"), 4242

    app, spawned = app_with(cfg, monkeypatch, answers, tmp_path)
    async with app.run_test() as pilot:
        await pilot.pause()
        app.run_worker(app.adder._settle(0, job_for(cfg, tmp_path)), exclusive=False)
        for _ in range(20):
            await pilot.pause()
            if spawned:
                break
    assert spawned and spawned[0]["title"] == "A Clip"
    assert spawned[0]["total"] == 4242


async def test_the_app_takes_its_results_from_the_adder(cfg, tmp_path, monkeypatch):
    """run_youtube reports on app.queued and app.cancelled, which the adder
    owns now — the handover has to actually happen."""
    app, spawned = app_with(cfg, monkeypatch, failing_probe, tmp_path)
    app.adder.queued = [{"url": "https://youtu.be/abc"}]
    app.adder.skipped = [{"url": "https://youtu.be/def"}]
    app.adder.cancelled = True
    async with app.run_test() as pilot:
        await pilot.pause()
        app._finished(app.adder)
        await pilot.pause()
    assert app.queued == [{"url": "https://youtu.be/abc"}]
    assert app.skipped == [{"url": "https://youtu.be/def"}]
    assert app.cancelled is True
