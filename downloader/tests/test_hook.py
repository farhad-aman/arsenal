import json
import shutil
import subprocess
import types

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
    """The URL says .iso so aria2 landed it in ISO/, but the real filename is
    .mkv — the only case relocation exists for."""
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", tmp_path / "Movies", ("mkv",), "🎬", "#fff")
    cats["iso"] = config.Category("iso", tmp_path / "ISO", ("iso",), "💿", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})

    landed = tmp_path / "ISO"
    landed.mkdir()
    src = landed / "movie.mkv"
    src.write_text("data")

    moved = hook.relocate(src, routed, "https://e.com/download.iso")
    assert moved == tmp_path / "Movies" / "movie.mkv"
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


def test_relocate_leaves_an_explicitly_chosen_directory_alone(tmp_path, cfg):
    picked = tmp_path / "my-custom-folder"
    picked.mkdir()
    src = picked / "movie.mkv"
    src.write_text("data")
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", tmp_path / "Movies", ("mkv",), "🎬", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})

    final = hook.relocate(src, routed, "https://e.com/movie.mkv")

    assert final == src
    assert src.exists()
    assert not (tmp_path / "Movies" / "movie.mkv").exists()


def test_drop_control_file_removes_the_aria2_sidecar(tmp_path):
    target = tmp_path / "movie.mkv"
    target.write_text("data")
    control = tmp_path / "movie.mkv.aria2"
    control.write_text("ctl")
    assert hook.drop_control_file(target) is True
    assert not control.exists()
    assert target.exists()


def test_drop_control_file_is_false_when_absent(tmp_path):
    assert hook.drop_control_file(tmp_path / "movie.mkv") is False


def test_main_cleans_control_files_at_both_old_and_new_locations(tmp_path, cfg, monkeypatch):
    target_dir = tmp_path / "Movies"
    cats = dict(cfg.categories)
    cats["video"] = config.Category("video", target_dir, ("mkv",), "🎬", "#fff")
    cats["iso"] = config.Category("iso", tmp_path / "ISO", ("iso",), "💿", "#fff")
    routed = config.Config(cfg.general, cfg.limits, cats, {})

    src_dir = tmp_path / "ISO"
    src_dir.mkdir()
    downloaded = src_dir / "movie.mkv"
    downloaded.write_text("data")
    (src_dir / "movie.mkv.aria2").write_text("ctl")

    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: routed)
    monkeypatch.setattr(hook, "notify", lambda *a: None)
    monkeypatch.setattr(hook, "arm_idle_shutdown", lambda *a: False)

    client = FakeClient()
    client.tell_status = lambda gid: status(
        files=[{"path": str(downloaded), "uris": [{"uri": "https://e.com/download.iso"}]}]
    )
    monkeypatch.setattr(hook, "Aria2", lambda *a, **k: client)

    assert hook.main(["complete", "g1", "1", str(downloaded)]) == 0
    assert (target_dir / "movie.mkv").exists()
    assert not (src_dir / "movie.mkv.aria2").exists()
    assert not (target_dir / "movie.mkv.aria2").exists()


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

    monkeypatch.setattr(hook, "Aria2", boom)
    assert hook.main(["complete", "g1", "1", str(tmp_path / "a.iso")]) == 0
    assert (tmp_path / "hook.log").exists()


def test_main_writes_history_row_on_success(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(hook, "STATE_DIR", tmp_path)
    monkeypatch.setattr(hook.config, "load", lambda *a, **k: cfg)
    monkeypatch.setattr(hook, "notify", lambda *a: None)
    monkeypatch.setattr(hook, "arm_idle_shutdown", lambda *a: False)

    client = FakeClient()
    client.tell_status = lambda gid: status()
    monkeypatch.setattr(hook, "Aria2", lambda *a, **k: client)

    assert hook.main(["complete", "g1", "1", "/tmp/a.iso"]) == 0
    rows = [json.loads(line) for line in (tmp_path / "history.jsonl").read_text().splitlines()]
    assert rows[0]["name"] == "a.iso"


def test_notify_quotes_the_applescript_way_not_the_python_way():
    """AppleScript string literals need double quotes. Python's !r emits single
    ones, which osascript rejects as a syntax error — silently, because notify
    captures output and never checks the code."""
    script = hook.notify_script("Download complete", "ubuntu.iso")
    assert script == 'display notification "ubuntu.iso" with title "Download complete"'


def test_notify_escapes_a_quote_in_the_filename():
    script = hook.notify_script("Download complete", 'a "quoted" file.iso')
    assert r'\"quoted\"' in script


def test_notify_escapes_a_backslash_in_the_filename():
    script = hook.notify_script("Download complete", r"we\ird.iso")
    assert r"we\\ird.iso" in script


def test_notify_keeps_non_ascii_titles_intact():
    """Most of what gets downloaded here is not ASCII."""
    assert "دختره دلمو" in hook.notify_script("Download complete", "دختره دلمو.mp4")


@pytest.mark.skipif(shutil.which("osascript") is None, reason="macOS only")
@pytest.mark.parametrize(
    "name",
    [
        "ubuntu.iso",
        'a "quoted" file.iso',
        r"we\ird.iso",
        "دختره دلمو شکوند ولی…💔.mp4",
        "Toxicity - System of a down SOAD ｜ Drumcover.mp4",
    ],
)
def test_osascript_actually_accepts_what_notify_builds(name):
    """The unit tests above only prove the string looks right. This one proves
    macOS parses it, which is the part that was broken."""
    done = subprocess.run(
        ["osascript", "-e", hook.notify_script("Download complete", name)],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr


def test_notify_reports_whether_it_got_through(monkeypatch):
    """Swallowing the result is why this went unnoticed for so long."""
    monkeypatch.setattr(
        hook.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="nope")
    )
    assert hook.notify("t", "b") is False


def test_the_record_remembers_it_went_through_a_proxy(cfg):
    record = hook.build_record(status(), "complete", cfg, proxied=True)
    assert record["proxy"] is True


def test_a_direct_download_is_recorded_as_direct(cfg):
    assert hook.build_record(status(), "complete", cfg)["proxy"] is False


@pytest.mark.parametrize("options,expected", [
    ({"all-proxy": "http://127.0.0.1:2080"}, True),
    ({"http-proxy": "http://127.0.0.1:2080"}, True),
    ({"all-proxy": ""}, False),
    ({}, False),
])
def test_proxy_is_read_from_whichever_option_carried_it(options, expected):
    assert hook.went_through_proxy(options) is expected


def hook_cfg(tmp_path, script_body, timeout=300):
    script = tmp_path / "on-complete.sh"
    script.write_text(script_body)
    script.chmod(0o755)
    base = config.defaults()
    return config.replace(base, on_complete=str(script), hook_timeout=timeout), script


def test_no_hook_configured_runs_nothing(cfg):
    assert hook.run_user_hook(cfg, {"path": "/tmp/a.iso"}) == ""


def test_the_hook_is_handed_the_file_category_and_url(tmp_path):
    out = tmp_path / "args"
    cfg, _ = hook_cfg(tmp_path, f'#!/bin/sh\nprintf "%s\\n" "$1" "$2" "$3" > {out}\n')
    problem = hook.run_user_hook(
        cfg, {"path": "/tmp/a.iso", "category": "iso", "url": "https://e.com/a.iso"}
    )
    assert problem == ""
    assert out.read_text().splitlines() == ["/tmp/a.iso", "iso", "https://e.com/a.iso"]


def test_a_hook_that_fails_says_why(tmp_path):
    cfg, _ = hook_cfg(tmp_path, '#!/bin/sh\necho "no such volume" >&2\nexit 3\n')
    assert "no such volume" in hook.run_user_hook(cfg, {"path": "/tmp/a.iso"})


def test_a_hook_that_fails_silently_still_reports(tmp_path):
    cfg, _ = hook_cfg(tmp_path, "#!/bin/sh\nexit 4\n")
    assert "4" in hook.run_user_hook(cfg, {"path": "/tmp/a.iso"})


def test_a_hook_that_hangs_is_cut_off(tmp_path):
    """A script waiting on a dead mount would otherwise pin a process forever."""
    cfg, _ = hook_cfg(tmp_path, "#!/bin/sh\nsleep 30\n", timeout=1)
    assert "timed out" in hook.run_user_hook(cfg, {"path": "/tmp/a.iso"})


def test_a_hook_that_is_not_there_reports_instead_of_raising(tmp_path):
    cfg = config.replace(config.defaults(), on_complete=str(tmp_path / "nope.sh"))
    problem = hook.run_user_hook(cfg, {"path": "/tmp/a.iso"})
    assert problem
    assert "nope.sh" in problem or "No such file" in problem


def test_the_hook_command_may_carry_its_own_arguments(tmp_path):
    out = tmp_path / "args"
    script = tmp_path / "h.sh"
    script.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > {out}\n')
    script.chmod(0o755)
    cfg = config.replace(config.defaults(), on_complete=f"{script} --verbose")
    hook.run_user_hook(cfg, {"path": "/tmp/a.iso", "category": "iso", "url": "u"})
    assert out.read_text().splitlines()[0] == "--verbose"


def test_a_filename_is_never_interpreted_by_a_shell(tmp_path):
    """Names come from the internet. `; rm -rf ~` has to arrive as text."""
    out = tmp_path / "args"
    canary = tmp_path / "canary"
    canary.write_text("intact")
    cfg, _ = hook_cfg(tmp_path, f'#!/bin/sh\nprintf "%s" "$1" > {out}\n')
    nasty = f"/tmp/a.iso; rm -f {canary}"
    assert hook.run_user_hook(cfg, {"path": nasty, "category": "", "url": ""}) == ""
    assert out.read_text() == nasty
    assert canary.read_text() == "intact"


def test_a_failing_hook_never_fails_the_download(tmp_path):
    """The bytes arrived. What the user asked to happen afterwards is a separate
    thing, and it going wrong must not rewrite the download as failed."""
    cfg, _ = hook_cfg(tmp_path, "#!/bin/sh\nexit 9\n")
    cfg = config.replace(cfg, general=config.replace(cfg.general, notify=False))
    record = {"name": "a.iso", "path": "/tmp/a.iso", "status": "ok"}
    problem = hook.after_complete(cfg, record, tmp_path / "state")
    assert problem
    assert record["status"] == "ok"


def test_a_failing_hook_is_written_where_it_can_be_found(tmp_path):
    cfg, _ = hook_cfg(tmp_path, '#!/bin/sh\necho "mount is gone" >&2\nexit 1\n')
    cfg = config.replace(cfg, general=config.replace(cfg.general, notify=False))
    state = tmp_path / "state"
    hook.after_complete(cfg, {"name": "a.iso", "path": "/tmp/a.iso"}, state)
    assert "mount is gone" in (state / "hook.log").read_text()


def test_a_hook_that_works_stays_quiet(tmp_path):
    cfg, _ = hook_cfg(tmp_path, "#!/bin/sh\nexit 0\n")
    state = tmp_path / "state"
    assert hook.after_complete(cfg, {"name": "a.iso", "path": "/tmp/a.iso"}, state) == ""
    assert not (state / "hook.log").exists()


def test_dropping_a_control_file_for_a_nameless_path_is_false():
    from pathlib import Path

    assert hook.drop_control_file(Path("")) is False
    assert hook.drop_control_file(Path(".")) is False
