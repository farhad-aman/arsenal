import pytest

from dl import youtube
from dl.youtube import Choices


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://www.youtube.com/shorts/abc123",
        "https://www.youtube.com/playlist?list=PL123",
        "http://youtube.com/watch?v=x",
    ],
)
def test_recognises_youtube_urls(url):
    assert youtube.is_youtube(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://e.com/movie.mkv",
        "https://notyoutube.com/watch?v=x",
        "https://youtube.com.evil.example/watch?v=x",
        "https://vimeo.com/12345",
        "magnet:?xt=urn:btih:abc",
        "",
    ],
)
def test_rejects_everything_else(url):
    assert youtube.is_youtube(url) is False


def test_defaults_are_highest_quality_no_subs():
    d = youtube.DEFAULTS
    assert d.video == "best"
    assert d.audio == "best"
    assert d.subs == "off"
    assert d.container == "mp4"


def test_best_video_and_audio_selects_merged_streams():
    args = youtube.build_args(Choices("best", "best", "off", "en", "mp4"))
    assert "-f" in args
    selector = args[args.index("-f") + 1]
    assert selector == "bv*+ba/b"


def test_capped_video_height_is_expressed_in_the_selector():
    args = youtube.build_args(Choices("1080", "best", "off", "en", "mp4"))
    selector = args[args.index("-f") + 1]
    assert "height<=1080" in selector


def test_container_becomes_the_merge_format():
    args = youtube.build_args(Choices("best", "best", "off", "en", "mkv"))
    assert "--merge-output-format" in args
    assert args[args.index("--merge-output-format") + 1] == "mkv"


def test_audio_only_extracts_audio_and_skips_merging():
    args = youtube.build_args(Choices("none", "best", "off", "en", "m4a"))
    assert "-x" in args
    assert args[args.index("--audio-format") + 1] == "m4a"
    assert "--merge-output-format" not in args
    assert args[args.index("-f") + 1] == "ba/b"


def test_audio_quality_sets_a_bitrate_when_not_best():
    args = youtube.build_args(Choices("none", "128", "off", "en", "mp3"))
    assert args[args.index("--audio-quality") + 1] == "128K"


def test_best_audio_leaves_the_bitrate_alone():
    assert "--audio-quality" not in youtube.build_args(Choices("none", "best", "off", "en", "m4a"))


def test_subtitles_off_writes_nothing():
    args = youtube.build_args(Choices("best", "best", "off", "en", "mp4"))
    assert not [a for a in args if "sub" in a]


def test_soft_subtitles_are_embedded_as_a_track():
    args = youtube.build_args(Choices("best", "best", "soft", "fa", "mkv"))
    assert "--embed-subs" in args
    assert args[args.index("--sub-langs") + 1] == "fa"
    assert "--write-subs" in args


def test_hard_subtitles_are_written_to_a_file_for_burning_in():
    """Burning in is an ffmpeg pass we run afterwards, so the track has to land
    on disk rather than be embedded."""
    args = youtube.build_args(Choices("best", "best", "hard", "en", "mp4"))
    assert "--write-subs" in args
    assert "--embed-subs" not in args
    assert args[args.index("--sub-langs") + 1] == "en"


def test_subtitles_always_allow_the_auto_generated_track():
    args = youtube.build_args(Choices("best", "best", "soft", "en", "mp4"))
    assert "--write-auto-subs" in args


def test_args_never_include_the_url_or_destination():
    """Those are added by the runner, which knows the picked folder."""
    args = youtube.build_args(youtube.DEFAULTS)
    assert not [a for a in args if a.startswith("http")]
    assert "-o" not in args


def test_burns_in_only_for_hard_subtitles():
    assert youtube.burns_in(Choices("best", "best", "hard", "en", "mp4")) is True
    assert youtube.burns_in(Choices("best", "best", "soft", "en", "mp4")) is False
    assert youtube.burns_in(Choices("best", "best", "off", "en", "mp4")) is False


def test_video_choices_offer_best_down_to_audio_only():
    assert youtube.VIDEO_CHOICES[0] == "best"
    assert "1080" in youtube.VIDEO_CHOICES
    assert youtube.VIDEO_CHOICES[-1] == "none"


def test_containers_differ_for_audio_only():
    assert "mp3" in youtube.AUDIO_CONTAINERS
    assert "mkv" in youtube.VIDEO_CONTAINERS
    assert "mp3" not in youtube.VIDEO_CONTAINERS
