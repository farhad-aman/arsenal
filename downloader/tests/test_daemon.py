import stat

import pytest

from dl import config, daemon


@pytest.fixture
def cfg():
    return config.defaults()


def test_read_secret_creates_file_with_0600(tmp_path):
    secret = daemon.read_secret(tmp_path)
    target = tmp_path / "rpc.secret"
    assert len(secret) >= 32
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_read_secret_is_stable_across_calls(tmp_path):
    assert daemon.read_secret(tmp_path) == daemon.read_secret(tmp_path)


def test_read_port_defaults_to_first_in_range(tmp_path):
    assert daemon.read_port(tmp_path) == daemon.PORT_RANGE.start


def test_write_then_read_port(tmp_path):
    daemon.write_port(tmp_path, 6815)
    assert daemon.read_port(tmp_path) == 6815


def test_read_port_ignores_garbage(tmp_path):
    (tmp_path / "port").write_text("not-a-port")
    assert daemon.read_port(tmp_path) == daemon.PORT_RANGE.start


def test_hook_shims_are_executable_and_exec_the_venv(tmp_path):
    complete, error = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    for path, mode in [(complete, "complete"), (error, "error")]:
        body = path.read_text()
        assert body.startswith("#!/bin/sh")
        assert "/opt/venv/bin/python" in body
        assert f"-m dl.hook {mode}" in body
        assert '"$@"' in body
        assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_hook_shims_pin_the_state_dir_so_a_fresh_hook_process_agrees(tmp_path):
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    assert f"DL_STATE_DIR={tmp_path}" in complete.read_text()


def test_hook_shims_pin_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DL_CONFIG_FILE", str(tmp_path / "custom.toml"))
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    assert f"DL_CONFIG_FILE={tmp_path / 'custom.toml'}" in complete.read_text()


def test_hook_shims_fall_back_to_the_real_config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DL_CONFIG_FILE", raising=False)
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    assert f"DL_CONFIG_FILE={config.CONFIG_FILE}" in complete.read_text()


def test_hook_shims_are_rewritten_when_python_moves(tmp_path):
    daemon.write_hook_shims(tmp_path, "/old/python")
    complete, _ = daemon.write_hook_shims(tmp_path, "/new/python")
    assert "/new/python" in complete.read_text()
    assert "/old/python" not in complete.read_text()


def test_generation_starts_at_zero_and_increments(tmp_path):
    assert daemon.read_generation(tmp_path) == 0
    assert daemon.bump_generation(tmp_path) == 1
    assert daemon.bump_generation(tmp_path) == 2
    assert daemon.read_generation(tmp_path) == 2


def test_generation_ignores_corrupt_file(tmp_path):
    (tmp_path / "generation").write_text("garbage")
    assert daemon.read_generation(tmp_path) == 0


def test_aria2_args_include_rpc_secret_and_localhost(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "abc")
    assert "--enable-rpc" in args
    assert "--rpc-secret=abc" in args
    assert "--rpc-listen-port=6810" in args
    assert "--rpc-listen-all=false" in args


def test_aria2_args_never_use_stop_with_process(tmp_path, cfg):
    assert not any("stop-with-process" in a for a in daemon.aria2_args(cfg, tmp_path, 6810, "x"))


def test_aria2_args_never_force_save(tmp_path, cfg):
    """--force-save keeps the .aria2 control file after a download completes."""
    assert not any("force-save" in a for a in daemon.aria2_args(cfg, tmp_path, 6810, "x"))


def test_aria2_args_apply_config_limits(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "x")
    assert "--max-concurrent-downloads=3" in args
    assert "--max-connection-per-server=16" in args
    assert "--split=16" in args
    assert "--min-split-size=1M" in args
    assert "--max-overall-download-limit=0" in args


def test_aria2_args_set_session_and_hooks(tmp_path, cfg):
    args = daemon.aria2_args(cfg, tmp_path, 6810, "x")
    assert f"--save-session={tmp_path / 'session'}" in args
    assert any(a.startswith("--on-download-complete=") for a in args)
    assert any(a.startswith("--on-download-error=") for a in args)
    assert "--auto-file-renaming=true" in args
    assert "--allow-overwrite=false" in args


def test_aria2_args_restore_session_only_when_present(tmp_path, cfg):
    assert not any(a.startswith("--input-file=") for a in daemon.aria2_args(cfg, tmp_path, 6810, "x"))
    (tmp_path / "session").write_text("")
    assert f"--input-file={tmp_path / 'session'}" in daemon.aria2_args(cfg, tmp_path, 6810, "x")


def test_bindable_is_false_while_a_socket_holds_the_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert daemon._bindable(port) is False
    assert daemon._bindable(port) is True


def test_ensure_running_skips_ports_that_cannot_be_bound(tmp_path, cfg, monkeypatch):
    blocked = daemon.PORT_RANGE.start
    monkeypatch.setattr(daemon.shutil, "which", lambda _: "/usr/bin/aria2c")
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: port != blocked)

    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda c, s, port, sec: spawned.append(port))
    monkeypatch.setattr(daemon, "_await_rpc", lambda port, sec, t: True)

    client = daemon.ensure_running(cfg, tmp_path)
    assert blocked not in spawned
    assert client.port == spawned[0]


def test_ensure_running_tries_the_next_port_when_a_spawn_never_answers(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda _: "/usr/bin/aria2c")
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: True)

    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda c, s, port, sec: spawned.append(port))
    monkeypatch.setattr(daemon, "_await_rpc", lambda port, sec, t: len(spawned) > 1)

    client = daemon.ensure_running(cfg, tmp_path)
    assert len(spawned) == 2
    assert client.port == spawned[1]


def test_ensure_running_without_binary_raises(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda _: None)
    with pytest.raises(daemon.Aria2Missing):
        daemon.ensure_running(cfg, tmp_path)


def test_corrupt_session_is_quarantined(tmp_path):
    session = tmp_path / "session"
    session.write_text("junk")
    daemon.quarantine_session(tmp_path)
    assert not session.exists()
    assert (tmp_path / "session.bad").read_text() == "junk"
