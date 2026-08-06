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


def test_read_port_defaults_to_the_one_port(tmp_path):
    assert daemon.read_port(tmp_path) == daemon.PORT


def test_write_then_read_port(tmp_path):
    daemon.write_port(tmp_path, 6815)
    assert daemon.read_port(tmp_path) == 6815


def test_read_port_ignores_garbage(tmp_path):
    (tmp_path / "port").write_text("not-a-port")
    assert daemon.read_port(tmp_path) == daemon.PORT


def test_hook_shims_are_executable_and_exec_the_venv(tmp_path):
    complete, error = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    for path, mode in [(complete, "complete"), (error, "error")]:
        body = path.read_text()
        assert body.startswith("#!/bin/sh")
        assert "/opt/venv/bin/python" in body
        assert f"-m dl.hook {mode}" in body
        assert '"$@"' in body
        assert stat.S_IMODE(path.stat().st_mode) == 0o755


def test_hook_shims_pass_the_state_dir_as_an_argument(tmp_path):
    """Not through the environment: nothing there can move dl's state, so the
    daemon has to tell its own hook where it keeps things."""
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    body = complete.read_text()
    assert f"--state {tmp_path}" in body
    assert "DL_STATE_DIR" not in body


def test_hook_shims_pass_the_config_file_as_an_argument(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "custom.toml")
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    body = complete.read_text()
    assert f"--config {tmp_path / 'custom.toml'}" in body
    assert "DL_CONFIG_FILE" not in body


def test_hook_shims_use_the_real_config_file_by_default(tmp_path):
    complete, _ = daemon.write_hook_shims(tmp_path, "/opt/venv/bin/python")
    assert f"--config {config.CONFIG_FILE}" in complete.read_text()


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
    assert "--max-download-limit=0" in args


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


def test_a_held_port_is_reported_rather_than_worked_around(tmp_path, cfg, monkeypatch):
    """dl used to move to the next free port. That is how daemons nothing
    could reach ended up running beside the real one."""
    monkeypatch.setattr(daemon.shutil, "which", lambda _: "/usr/bin/aria2c")
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: False)

    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda c, s, port, sec: spawned.append(port))
    with pytest.raises(daemon.DaemonStartFailed):
        daemon.ensure_running(cfg, tmp_path)
    assert spawned == []


def test_a_spawn_that_never_answers_fails_on_the_one_port(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda _: "/usr/bin/aria2c")
    monkeypatch.setattr(daemon, "_probe", lambda port, secret: "free")
    monkeypatch.setattr(daemon, "_bindable", lambda port: True)

    spawned = []
    monkeypatch.setattr(daemon, "_spawn", lambda c, s, port, sec: spawned.append(port))
    monkeypatch.setattr(daemon, "_await_rpc", lambda port, sec, t: False)

    with pytest.raises(daemon.DaemonStartFailed):
        daemon.ensure_running(cfg, tmp_path)
    assert spawned == [daemon.PORT]


def test_ensure_running_without_binary_raises(tmp_path, cfg, monkeypatch):
    monkeypatch.setattr(daemon.shutil, "which", lambda _: None)
    with pytest.raises(daemon.Aria2Missing):
        daemon.ensure_running(cfg, tmp_path)


def test_spawn_env_drops_ambient_proxy_settings(monkeypatch):
    """A shell with `vpn -p` active would otherwise proxy every download, and the
    daemon outlives the shell that spawned it. -p alone decides proxying."""
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:2080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:2080")
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:2080")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = daemon.spawn_env()

    assert env["PATH"] == "/usr/bin"
    assert not [k for k in env if "proxy" in k.lower()]


def test_corrupt_session_is_quarantined(tmp_path):
    session = tmp_path / "session"
    session.write_text("junk")
    daemon.quarantine_session(tmp_path)
    assert not session.exists()
    assert (tmp_path / "session.bad").read_text() == "junk"


def test_aria2_args_never_set_a_global_rate_limit(tmp_path, cfg):
    """Limits are per-download; a daemon-wide cap would throttle everything."""
    args = daemon.aria2_args(cfg, tmp_path, 6810, "x")
    assert not any("max-overall-download-limit" in a for a in args)
