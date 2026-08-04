import json

import pytest

from dl import config, hook


@pytest.fixture
def cfg():
    return config.defaults()


def status(**over):
    base = {
        "gid": "g1",
        "status": "complete",
        "totalLength": "1048576",
        "completedLength": "1048576",
        "downloadSpeed": "0",
        "files": [{"path": "/tmp/a.iso", "uris": [{"uri": "https://e.com/a.iso"}]}],
        "errorCode": "0",
        "errorMessage": "",
    }
    base.update(over)
    return base


def test_build_record_converts_string_numbers(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["bytes"] == 1048576
    assert isinstance(rec["bytes"], int)
    assert rec["status"] == "ok"


def test_build_record_captures_name_url_and_category(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["name"] == "a.iso"
    assert rec["url"] == "https://e.com/a.iso"
    assert rec["category"] == "iso"


def test_build_record_for_error_carries_message(cfg):
    rec = hook.build_record(
        status(status="error", errorCode="22", errorMessage="HTTP 403"), "error", cfg
    )
    assert rec["status"] == "error"
    assert rec["error"] == "HTTP 403"


def test_build_record_handles_missing_files_list(cfg):
    rec = hook.build_record(status(files=[]), "complete", cfg)
    assert rec["name"] == ""
    assert rec["url"] == ""


def test_build_record_names_a_failure_that_never_got_a_path(cfg):
    rec = hook.build_record(
        status(
            status="error",
            errorMessage="SSL/TLS handshake failure",
            files=[{"path": "", "uris": [{"uri": "https://e.com/100MB.bin"}]}],
        ),
        "error",
        cfg,
    )
    assert rec["name"] == "100MB.bin"
    assert rec["path"] == ""


def test_build_record_avg_bps_is_zero_when_instant(cfg):
    rec = hook.build_record(status(), "complete", cfg)
    assert rec["avg_bps"] >= 0


def test_relocate_moves_file_when_category_changes(tmp_path, cfg):
    src_dir = tmp_path / "wrong"
    src_dir.mkdir()
    src = src_dir / "movie.mkv"
    src.write_text("data")
    target_dir = tmp_path / "right"
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert moved == target_dir / "movie.mkv"
    assert moved.read_text() == "data"
    assert not src.exists()


def test_relocate_is_a_noop_when_already_correct(tmp_path, cfg):
    target_dir = tmp_path / "vids"
    target_dir.mkdir()
    src = target_dir / "movie.mkv"
    src.write_text("data")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert moved == src
    assert src.exists()


def test_relocate_does_not_clobber_existing_file(tmp_path, cfg):
    src_dir = tmp_path / "a"
    src_dir.mkdir()
    src = src_dir / "movie.mkv"
    src.write_text("new")
    target_dir = tmp_path / "b"
    target_dir.mkdir()
    (target_dir / "movie.mkv").write_text("existing")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    moved = hook.relocate(src, config.Config(cfg.general, cfg.limits, cats, {}), "https://e.com/movie.mkv")
    assert (target_dir / "movie.mkv").read_text() == "existing"
    assert moved.read_text() == "new"
    assert moved != target_dir / "movie.mkv"


def test_relocate_returns_original_when_file_is_missing(tmp_path, cfg):
    ghost = tmp_path / "ghost.mkv"
    assert hook.relocate(ghost, cfg, "https://e.com/ghost.mkv") == ghost


class FakeClient:
    def __init__(self, active=(), waiting=()):
        self._active = list(active)
        self._waiting = list(waiting)
        self.shutdown_called = False

    def tell_active(self):
        return self._active

    def tell_waiting(self, offset=0, num=1000):
        return self._waiting

    def shutdown(self):
        self.shutdown_called = True
        return "OK"


def test_arm_idle_shutdown_skips_when_queue_is_busy(tmp_path, cfg):
    assert hook.arm_idle_shutdown(FakeClient(active=[{"gid": "g"}]), cfg, tmp_path) is False


def test_arm_idle_shutdown_arms_when_queue_is_empty(tmp_path, cfg, monkeypatch):
    spawned = []
    monkeypatch.setattr(hook, "_spawn_sleeper", lambda *a: spawned.append(a))
    assert hook.arm_idle_shutdown(FakeClient(), cfg, tmp_path) is True
    assert spawned


def test_main_appends_history_and_survives_rpc_failure(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: cfg)

    def boom(*_a, **_k):
        raise RuntimeError("no daemon")

    monkeypatch.setattr(hook.daemon, "ensure_running", boom)
    assert hook.main(["complete", "g1", "1", str(tmp_path / "a.iso")]) == 0
    assert (tmp_path / "hook.log").exists()


def test_main_writes_history_row_on_success(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(hook, "notify", lambda *a: None)
    monkeypatch.setattr(hook, "arm_idle_shutdown", lambda *a: False)

    client = FakeClient()
    client.tell_status = lambda gid: status()
    monkeypatch.setattr(hook.daemon, "ensure_running", lambda *a, **k: client)

    assert hook.main(["complete", "g1", "1", "/tmp/a.iso"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert rows[0]["name"] == "a.iso"
