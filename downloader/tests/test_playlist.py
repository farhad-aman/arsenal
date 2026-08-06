import pytest

from dl import playlist


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/playlist?list=PLxyz",
        "https://youtube.com/playlist?list=PLxyz",
        "https://music.youtube.com/playlist?list=OLAK5uy_abc",
        "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
        "https://www.youtube.com/channel/UCabc/videos",
        "https://www.youtube.com/@someone",
        "https://www.youtube.com/@someone/videos",
        "https://www.youtube.com/@someone/shorts",
        "https://www.youtube.com/@someone/streams",
        "https://www.youtube.com/c/SomeChannel",
        "https://www.youtube.com/user/SomeUser",
    ],
)
def test_a_collection_url_is_recognised(url):
    assert playlist.is_collection(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/abc123",
        "https://www.youtube.com/shorts/abc123",
        "https://music.youtube.com/watch?v=abc123",
    ],
)
def test_a_single_video_is_not_a_collection(url):
    assert playlist.is_collection(url) is False


def test_a_video_copied_from_inside_a_playlist_is_still_one_video():
    """The common case: you were watching something and copied the address."""
    url = "https://www.youtube.com/watch?v=abc123&list=PLxyz&index=4"
    assert playlist.is_collection(url) is False


def test_a_youtu_be_link_carrying_a_list_is_still_one_video():
    assert playlist.is_collection("https://youtu.be/abc123?list=PLxyz") is False


def test_a_non_youtube_url_is_not_a_collection():
    assert playlist.is_collection("https://e.com/a.iso") is False


def test_an_empty_url_is_not_a_collection():
    assert playlist.is_collection("") is False


def test_a_malformed_url_is_not_a_collection():
    assert playlist.is_collection("http://[") is False


def test_entries_are_parsed_from_the_listing():
    output = "https://youtu.be/a\tFirst video\nhttps://youtu.be/b\tSecond video\n"
    entries = playlist.parse_entries(output)
    assert [e.url for e in entries] == ["https://youtu.be/a", "https://youtu.be/b"]
    assert [e.title for e in entries] == ["First video", "Second video"]


def test_an_entry_without_a_title_keeps_its_url():
    entries = playlist.parse_entries("https://youtu.be/a\t\n")
    assert entries[0].title == ""
    assert entries[0].url == "https://youtu.be/a"


def test_blank_lines_are_ignored():
    assert len(playlist.parse_entries("\n\nhttps://youtu.be/a\tOne\n\n")) == 1


def test_a_line_without_a_separator_is_dropped():
    """NA or a warning that slipped into stdout is not an entry."""
    assert playlist.parse_entries("NA\nhttps://youtu.be/a\tOne\n") == [
        playlist.Entry("https://youtu.be/a", "One")
    ]


def test_a_title_containing_a_tab_keeps_it():
    """Only the last field is the collection name, so a tab in the middle
    belongs to the title."""
    entries = playlist.parse_entries("https://youtu.be/a\tone\ttwo\tWeekly\n")
    assert entries[0].title == "one\ttwo"


def test_an_entry_that_is_not_a_url_is_dropped():
    assert playlist.parse_entries("not-a-url\tTitle\n") == []


def test_the_listing_command_asks_for_url_and_title():
    argv = playlist.list_command("https://youtube.com/playlist?list=PL", "", "")
    assert "--flat-playlist" in argv
    assert argv[argv.index("--print") + 1].startswith("%(url)s\t%(title)s")
    assert argv[-1] == "https://youtube.com/playlist?list=PL"


def test_the_listing_command_carries_the_proxy_and_cookies():
    argv = playlist.list_command("https://youtube.com/playlist?list=PL", "http://p:1", "chrome")
    assert "--proxy" in argv and "http://p:1" in argv
    assert "--cookies-from-browser" in argv and "chrome" in argv


def test_the_listing_command_can_stop_early():
    argv = playlist.list_command("https://youtube.com/playlist?list=PL", "", "", limit=25)
    assert "--playlist-end" in argv
    assert "25" in argv


def test_the_listing_command_without_a_limit_asks_for_everything():
    argv = playlist.list_command("https://youtube.com/playlist?list=PL", "", "")
    assert "--playlist-end" not in argv


def test_the_listing_command_never_asks_for_a_single_video():
    """--no-playlist here would defeat the whole point."""
    argv = playlist.list_command("https://youtube.com/playlist?list=PL", "", "")
    assert "--no-playlist" not in argv


class Result:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


def test_expand_returns_the_entries(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess,
        "run",
        lambda *a, **k: Result("https://youtu.be/a\tOne\nhttps://youtu.be/b\tTwo\n"),
    )
    entries = playlist.expand("https://youtube.com/playlist?list=PL", "", "")
    assert [e.title for e in entries] == ["One", "Two"]


def test_expand_reports_an_empty_listing(monkeypatch):
    monkeypatch.setattr(
        playlist.subprocess, "run", lambda *a, **k: Result("", "ERROR: private playlist")
    )
    with pytest.raises(playlist.ListingFailed) as exc:
        playlist.expand("https://youtube.com/playlist?list=PL", "", "")
    assert "private" in str(exc.value)


def test_expand_reports_a_timeout(monkeypatch):
    def slow(*a, **k):
        raise playlist.subprocess.TimeoutExpired("yt-dlp", 120)

    monkeypatch.setattr(playlist.subprocess, "run", slow)
    with pytest.raises(playlist.ListingFailed) as exc:
        playlist.expand("https://youtube.com/playlist?list=PL", "", "")
    assert "timed out" in str(exc.value)


def test_expand_reports_a_missing_yt_dlp(monkeypatch):
    def missing(*a, **k):
        raise OSError("No such file or directory: 'yt-dlp'")

    monkeypatch.setattr(playlist.subprocess, "run", missing)
    with pytest.raises(playlist.ListingFailed):
        playlist.expand("https://youtube.com/playlist?list=PL", "", "")
