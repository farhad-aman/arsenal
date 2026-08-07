"""Checking a checksum before aria2 gets a chance to be unhelpful about it."""

import pytest

from dl import checksum

SHA256 = "062af9ccd890ba3d067ca7150278bcc420069bd82f6e41161029303dfd6d661e"
MD5 = "6134696ca1b050d4564d58a18aa9d35a"


def test_a_plain_sha256_is_taken():
    assert checksum.parse(f"sha256={SHA256}") == ("sha-256", SHA256)


def test_arias_own_spelling_is_taken_too():
    assert checksum.parse(f"sha-256={SHA256}") == ("sha-256", SHA256)


def test_the_algorithm_is_case_insensitive():
    assert checksum.parse(f"SHA256={SHA256}")[0] == "sha-256"


def test_the_digest_is_case_insensitive():
    assert checksum.parse(f"sha256={SHA256.upper()}")[1] == SHA256


def test_surrounding_space_is_ignored():
    assert checksum.parse(f"  sha256 = {SHA256}  ")[1] == SHA256


def test_md5_still_works_for_the_sites_that_only_publish_it():
    assert checksum.parse(f"md5={MD5}") == ("md5", MD5)


def test_every_algorithm_aria2_was_built_with_is_accepted():
    for name, length in checksum.ALGORITHMS.items():
        assert checksum.parse(f"{name}={'a' * length}")[0] == name


def test_normalise_returns_what_aria2_takes():
    assert checksum.normalise(f"sha256={SHA256}") == f"sha-256={SHA256}"


def test_something_with_no_equals_is_refused():
    with pytest.raises(checksum.Invalid, match="algorithm"):
        checksum.parse(SHA256)


def test_an_unknown_algorithm_is_refused():
    with pytest.raises(checksum.Invalid, match="unknown algorithm"):
        checksum.parse(f"sha999={SHA256}")


def test_a_digest_that_is_not_hexadecimal_is_refused():
    with pytest.raises(checksum.Invalid, match="hexadecimal"):
        checksum.parse("sha256=" + "z" * 64)


def test_a_truncated_digest_is_refused():
    """The usual way a checksum goes wrong is half a paste."""
    with pytest.raises(checksum.Invalid, match="64 characters, this is 40"):
        checksum.parse(f"sha256={SHA256[:40]}")


def test_an_empty_digest_is_refused():
    with pytest.raises(checksum.Invalid):
        checksum.parse("sha256=")


def test_a_failed_verification_is_recognised_by_its_code():
    assert checksum.mismatched({"errorCode": "32"}) is True
    assert checksum.mismatched({"errorCode": 32}) is True


def test_another_failure_is_not_a_checksum_failure():
    assert checksum.mismatched({"errorCode": "1"}) is False
    assert checksum.mismatched({}) is False


def test_a_mismatch_gets_words_because_aria2_gives_none():
    """aria2 reports code 32 with an empty message, so the row would otherwise
    say "error" and nothing else."""
    assert checksum.explain({"errorCode": "32", "errorMessage": ""}) == checksum.MISMATCH


def test_a_real_message_is_left_alone():
    assert checksum.explain({"errorCode": "1", "errorMessage": "HTTP 404"}) == "HTTP 404"


def test_a_failure_with_neither_gets_nothing_invented():
    assert checksum.explain({"errorCode": "1", "errorMessage": ""}) == ""


def _failed(**over):
    base = {
        "gid": "g1",
        "status": "error",
        "errorCode": "32",
        "errorMessage": "",
        "totalLength": "1000",
        "downloadSpeed": "0",
        "files": [{"path": "", "uris": [{"uri": "https://e.com/a.iso"}]}],
    }
    base.update(over)
    return base


def test_the_bad_bytes_are_removed(tmp_path):
    """A file that failed its checksum is provably not what was asked for, and
    leaving it complete on disk means a retry resumes it and changes nothing."""
    from dl import hook

    landed = tmp_path / "a.iso"
    landed.write_bytes(b"wrong")
    control = tmp_path / "a.iso.aria2"
    control.write_bytes(b"x")

    hook.discard_corrupt(_failed(files=[{"path": str(landed), "uris": []}]))
    assert not landed.exists()
    assert not control.exists()


def test_a_file_that_failed_for_another_reason_is_left_alone(tmp_path):
    from dl import hook

    landed = tmp_path / "a.iso"
    landed.write_bytes(b"partial")
    hook.discard_corrupt(_failed(errorCode="1", files=[{"path": str(landed), "uris": []}]))
    assert landed.exists()


def test_discarding_says_so_in_the_record(sandbox_cfg):
    from dl import hook

    record = hook.build_record(_failed(), "error", sandbox_cfg)
    assert checksum.MISMATCH in record["error"]


def test_discarding_a_file_that_is_already_gone_is_quiet(tmp_path):
    from dl import hook

    hook.discard_corrupt(_failed(files=[{"path": str(tmp_path / "never"), "uris": []}]))


def test_a_completed_download_is_never_discarded(tmp_path):
    from dl import hook

    landed = tmp_path / "a.iso"
    landed.write_bytes(b"good")
    hook.discard_corrupt({"status": "complete", "files": [{"path": str(landed)}]})
    assert landed.exists()
