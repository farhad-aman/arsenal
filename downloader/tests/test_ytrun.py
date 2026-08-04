from dl import ytrun


def test_last_error_prefers_the_error_line(tmp_path):
    log = tmp_path / "job.log"
    log.write_text(
        "[youtube] Extracting URL: https://youtu.be/x\n"
        "[youtube] x: Downloading webpage\n"
        "ERROR: [youtube] x: Sign in to confirm you're not a bot. Use --cookies\n"
    )
    assert "Sign in to confirm" in ytrun.last_error(log)
    assert not ytrun.last_error(log).startswith("ERROR:")


def test_last_error_falls_back_to_the_final_line(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("something went sideways\n")
    assert ytrun.last_error(log) == "something went sideways"


def test_last_error_of_a_missing_log_is_empty(tmp_path):
    assert ytrun.last_error(tmp_path / "nope.log") == ""


def test_last_error_of_an_empty_log_is_empty(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("")
    assert ytrun.last_error(log) == ""


def test_last_error_is_capped(tmp_path):
    log = tmp_path / "job.log"
    log.write_text("ERROR: " + "x" * 5000)
    assert len(ytrun.last_error(log)) <= 300
