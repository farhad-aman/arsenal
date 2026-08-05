import functools
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from dl import config, daemon, history, routing
from dl.cli import add_options
from dl.rpc import Aria2

pytestmark = pytest.mark.skipif(shutil.which("aria2c") is None, reason="aria2c not installed")

PAYLOAD = b"x" * (5 * 1024 * 1024)


@pytest.fixture
def fileserver(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    (root / "sample.iso").write_bytes(PAYLOAD)
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(root))
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    downloads = tmp_path / "downloads"
    base = config.defaults()
    cats = dict(base.categories)
    cats["iso"] = config.Category("iso", downloads / "ISO", ("iso",), "💿", "#4aa3ff")
    general = config.replace(base.general, default_dir=downloads / "other", idle_timeout=2)
    cfg = config.Config(general, base.limits, cats, {})

    config_file = tmp_path / "config.toml"
    config_file.write_text(
        f'[general]\ndefault_dir = "{downloads / "other"}"\nidle_timeout = "2s"\n'
        f'notify = false\n\n'
        f'[categories.iso]\ndir = "{downloads / "ISO"}"\next = ["iso"]\n'
        f'icon = "💿"\nhue = "#4aa3ff"\n'
    )
    monkeypatch.setenv("DL_STATE_DIR", str(state))
    monkeypatch.setenv("DL_CONFIG_FILE", str(config_file))
    monkeypatch.setattr(config, "STATE_DIR", state)
    monkeypatch.setattr(config, "CONFIG_FILE", config_file)
    monkeypatch.setattr(daemon, "STATE_DIR", state)
    yield cfg, state
    try:
        Aria2("127.0.0.1", daemon.read_port(state), daemon.read_secret(state), timeout=1).shutdown()
    except Exception:
        pass
    for _ in range(60):
        if daemon._bindable(daemon.read_port(state)):
            break
        time.sleep(0.1)


def queue(cfg, client, url, name, **extra):
    resolution = routing.resolve(url, name, cfg)
    resolution.path.mkdir(parents=True, exist_ok=True)
    return client.add_uri([url], {**add_options(cfg, resolution), **extra})


def wait_for(predicate, timeout=30.0, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def test_daemon_starts_and_answers(env):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    assert "version" in client.get_version()
    assert (state / "rpc.secret").exists()
    assert (state / "hooks" / "complete.sh").exists()


def test_second_ensure_running_reuses_the_same_daemon(env):
    cfg, state = env
    first = daemon.ensure_running(cfg, state)
    second = daemon.ensure_running(cfg, state)
    assert first.port == second.port


def test_full_download_lands_in_the_routed_directory(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    gid = queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    target = cfg.categories["iso"].dir / "sample.iso"
    assert wait_for(lambda: target.exists() and target.stat().st_size == len(PAYLOAD))
    assert client.tell_status(gid)["status"] == "complete"


def test_completed_download_leaves_no_aria2_control_file(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    target = cfg.categories["iso"].dir / "sample.iso"
    assert wait_for(lambda: target.exists() and target.stat().st_size == len(PAYLOAD))
    assert wait_for(lambda: not list(target.parent.glob("*.aria2")), timeout=15)


def test_hook_writes_a_history_row(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    log = state / "history.jsonl"
    assert wait_for(lambda: log.exists() and history.tail(log, 5))
    record = history.tail(log, 5)[-1]
    assert record["name"] == "sample.iso"
    assert record["status"] == "ok"
    assert record["bytes"] == len(PAYLOAD)


def test_pause_then_resume_mid_transfer(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    client.change_global_option({"max-overall-download-limit": "64K"})
    gid = queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    assert wait_for(lambda: client.tell_status(gid)["status"] == "active")
    client.pause(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] == "paused")
    client.unpause(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] in ("active", "waiting"))
    client.change_global_option({"max-overall-download-limit": "0"})


def test_remove_deletes_from_the_queue(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    client.change_global_option({"max-overall-download-limit": "64K"})
    gid = queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")
    assert wait_for(lambda: client.tell_status(gid)["status"] == "active")
    client.remove(gid)
    assert wait_for(lambda: client.tell_status(gid)["status"] in ("removed", "error"))
    client.change_global_option({"max-overall-download-limit": "0"})


def test_failed_download_records_an_error_row(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    queue(cfg, client, f"{fileserver}/missing.iso", "missing.iso", **{"max-tries": "1"})

    log = state / "history.jsonl"
    assert wait_for(
        lambda: log.exists() and any(r["status"] == "error" for r in history.tail(log, 10)),
        timeout=40,
    )


async def test_preview_exits_when_a_real_download_finishes(env, fileserver):
    import asyncio

    from dl.tui.preview import PreviewApp

    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    gid = queue(cfg, client, f"{fileserver}/sample.iso", "sample.iso")

    app = PreviewApp(cfg, client, [gid])
    async with app.run_test() as pilot:
        for _ in range(300):
            await app.refresh_data()
            if not app.is_running:
                break
            await asyncio.sleep(0.1)

    target = cfg.categories["iso"].dir / "sample.iso"
    assert target.exists() and target.stat().st_size == len(PAYLOAD)
    assert app.results and app.results[0]["status"] == "complete"


def test_rpc_is_never_exposed_beyond_loopback(env):
    cfg, state = env
    args = daemon.aria2_args(cfg, state, 6810, "x")
    assert "--rpc-listen-all=false" in args


def test_a_picked_destination_survives_completion(env, fileserver):
    cfg, state = env
    client = daemon.ensure_running(cfg, state)
    picked = state.parent / "hand-picked"
    picked.mkdir()

    url = f"{fileserver}/sample.iso"
    gid = client.add_uri(
        [url], {**add_options(cfg, routing.resolve(url, "sample.iso", cfg)), "dir": str(picked)}
    )

    target = picked / "sample.iso"
    assert wait_for(lambda: target.exists() and target.stat().st_size == len(PAYLOAD))
    assert wait_for(lambda: client.tell_status(gid)["status"] == "complete")

    log = state / "history.jsonl"
    assert wait_for(lambda: log.exists() and history.tail(log, 5))
    record = history.tail(log, 5)[-1]
    assert Path(record["path"]).parent == picked
    assert not (cfg.categories["iso"].dir / "sample.iso").exists()


def fake_ytdlp(directory: Path) -> Path:
    """A yt-dlp that simulates instantly and then downloads forever."""
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "yt-dlp"
    script.write_text(
        "#!/bin/sh\n"
        'case " $* " in *" --simulate "*) echo clip; echo /tmp/clip.mp4; echo 100; exit 0;; esac\n'
        "sleep 120\n"
    )
    script.chmod(0o755)
    return script


def test_pausing_a_youtube_job_is_not_recorded_as_a_failure(tmp_path, monkeypatch):
    """The supervisor has to let go of yt-dlp itself. Signalling it from the
    dashboard would race the poll loop into finalize(), and a pause would come
    back as an error the user is invited to retry."""
    from dl import ytjob
    from dl.youtube import DEFAULTS

    state = tmp_path / "state"
    jobs = state / "yt"
    env = dict(os.environ, DL_STATE_DIR=str(state), XDG_CONFIG_HOME=str(tmp_path / "cfg"))
    env["PATH"] = f"{fake_ytdlp(tmp_path / 'bin')}".rsplit("/", 1)[0] + os.pathsep + env["PATH"]

    job = ytjob.new_job("https://youtu.be/abc", tmp_path / "out", DEFAULTS)
    record = ytjob.save(jobs, job)

    supervisor = subprocess.Popen(
        [sys.executable, "-m", "dl.ytrun", str(record)],
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        assert wait_for(lambda: ytjob.read(record).get("status") == "active", timeout=20)
        downloader = ytjob.read(record)["pid"]
        assert ytjob.running(downloader)

        ytjob.pause(jobs, ytjob.read(record))

        assert wait_for(lambda: supervisor.poll() is not None, timeout=20), supervisor.stdout.read()
        assert supervisor.returncode == 0
        assert wait_for(lambda: not ytjob.running(downloader), timeout=10)
        assert ytjob.read(record)["status"] == "paused"
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
