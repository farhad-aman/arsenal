from collections import deque

import pytest

from dl import config, watch


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class FakeClient:
    def __init__(self):
        self.added = []
        self.calls = []
        self.active = []

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        self.calls.append((uris, options))
        return "gid"

    def tell_active(self):
        return self.active

    def tell_waiting(self, offset=0, num=1000):
        return []


@pytest.mark.parametrize(
    "text,ok",
    [
        ("https://e.com/a.iso", True),
        ("http://e.com/a.iso", True),
        ("magnet:?xt=urn:btih:abc", True),
        ("ftp://e.com/a.iso", True),
        ("just some text", False),
        ("", False),
        ("   ", False),
        ("https://e.com/a.iso and more words", False),
        ("file:///etc/passwd", False),
    ],
)
def test_is_downloadable(text, ok):
    assert watch.is_downloadable(text) is ok


def test_poll_once_queues_a_new_url(cfg):
    client = FakeClient()
    assert watch.poll_once("https://e.com/a.iso", deque(maxlen=20), cfg, client) is True
    assert client.added == ["https://e.com/a.iso"]


def test_poll_once_ignores_repeat_of_same_url(cfg):
    client = FakeClient()
    seen = deque(maxlen=20)
    watch.poll_once("https://e.com/a.iso", seen, cfg, client)
    assert watch.poll_once("https://e.com/a.iso", seen, cfg, client) is False
    assert len(client.added) == 1


def test_poll_once_ignores_non_urls(cfg):
    client = FakeClient()
    assert watch.poll_once("hello", deque(maxlen=20), cfg, client) is False
    assert not client.added


def test_seen_ring_forgets_beyond_twenty(cfg):
    client = FakeClient()
    seen = deque(maxlen=20)
    watch.poll_once("https://e.com/first.iso", seen, cfg, client)
    for i in range(20):
        watch.poll_once(f"https://e.com/{i}.iso", seen, cfg, client)
    assert watch.poll_once("https://e.com/first.iso", seen, cfg, client) is True


def test_run_drives_the_reader_for_n_iterations(cfg):
    client = FakeClient()
    clips = iter(["https://e.com/a.iso", "https://e.com/a.iso", "https://e.com/b.mkv"])
    watch.run(cfg, client, interval=0, reader=lambda: next(clips, ""), iterations=3)
    assert client.added == ["https://e.com/a.iso", "https://e.com/b.mkv"]


def status(path, url, gid="g1"):
    return {
        "gid": gid,
        "status": "active",
        "totalLength": "100",
        "files": [{"path": str(path), "uris": [{"uri": url}]}],
    }


def test_a_caught_url_on_a_listed_domain_is_proxied(cfg):
    """`dl watch` has no -p to pass, so without domain rules a filtered host is
    caught and then fails."""
    client = FakeClient()
    proxied = config.replace(cfg, proxy_domains=("e.com",))
    watch.poll_once("https://e.com/a.iso", deque(maxlen=20), proxied, client)
    assert client.calls[0][1]["all-proxy"] == cfg.proxy


def test_a_caught_url_on_an_unlisted_domain_stays_direct(cfg):
    client = FakeClient()
    watch.poll_once("https://e.com/a.iso", deque(maxlen=20), cfg, client)
    assert "all-proxy" not in client.calls[0][1]


def test_a_url_already_on_disk_is_not_queued_again(cfg, capsys):
    """Nothing here can prompt, so the safe answer is to leave it alone and say
    so rather than silently write a second copy."""
    client = FakeClient()
    target = cfg.categories["iso"].dir
    target.mkdir(parents=True, exist_ok=True)
    (target / "a.iso").write_bytes(b"already here")

    assert watch.poll_once("https://e.com/a.iso", deque(maxlen=20), cfg, client) is False
    assert client.added == []
    assert "skipped" in capsys.readouterr().out


def test_a_url_already_downloading_is_not_queued_again(cfg):
    client = FakeClient()
    client.active = [status(cfg.categories["iso"].dir / "a.iso", "https://e.com/a.iso")]
    assert watch.poll_once("https://e.com/a.iso", deque(maxlen=20), cfg, client) is False
    assert client.added == []


def test_a_youtube_link_goes_to_yt_dlp_not_aria2(cfg, monkeypatch, tmp_path):
    """aria2 would fetch the watch page itself and save it as HTML."""
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None, cap=0: spawned.append(job))
    monkeypatch.setattr("dl.ytrun.probe", lambda job, timeout=None: ("A Clip", str(tmp_path / "A Clip.mp4"), 99))
    monkeypatch.setattr(watch.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")

    client = FakeClient()
    assert watch.poll_once("https://youtu.be/abc", deque(maxlen=20), cfg, client) is True
    assert client.added == []
    assert spawned[0]["url"] == "https://youtu.be/abc"
    assert spawned[0]["title"] == "A Clip"


def test_a_caught_youtube_link_on_a_listed_domain_is_proxied(cfg, monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None, cap=0: spawned.append(job))
    monkeypatch.setattr("dl.ytrun.probe", lambda job, timeout=None: ("A Clip", str(tmp_path / "A Clip.mp4"), 99))
    monkeypatch.setattr(watch.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")

    proxied = config.replace(cfg, proxy_domains=("youtu.be",))
    watch.poll_once("https://youtu.be/abc", deque(maxlen=20), proxied, FakeClient())
    assert spawned[0]["proxy"] == cfg.proxy


def test_a_youtube_video_already_on_disk_is_not_fetched_again(cfg, monkeypatch, tmp_path):
    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None, cap=0: spawned.append(job))
    landed = tmp_path / "A Clip.mp4"
    landed.write_bytes(b"already here")
    monkeypatch.setattr("dl.ytrun.probe", lambda job, timeout=None: ("A Clip", str(landed), 99))
    monkeypatch.setattr(watch.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")

    assert watch.poll_once("https://youtu.be/abc", deque(maxlen=20), cfg, FakeClient()) is False
    assert spawned == []


def test_a_youtube_link_without_yt_dlp_says_so(cfg, monkeypatch, capsys):
    monkeypatch.setattr(watch.shutil, "which", lambda name: None)
    client = FakeClient()
    assert watch.poll_once("https://youtu.be/abc", deque(maxlen=20), cfg, client) is False
    assert client.added == []
    assert "yt-dlp" in capsys.readouterr().out


def test_a_youtube_link_it_cannot_check_is_left_alone(cfg, monkeypatch, capsys):
    """Queuing blind would let yt-dlp find the file already there and do
    nothing, which is the silence this check exists to remove."""
    from dl import ytrun

    spawned = []
    monkeypatch.setattr("dl.tui.ytflow.spawn", lambda job, state=None, cap=0: spawned.append(job))
    monkeypatch.setattr(watch.shutil, "which", lambda name: "/usr/local/bin/yt-dlp")

    def die(job, timeout=None):
        raise ytrun.ProbeFailed("timed out after 180s")

    monkeypatch.setattr("dl.ytrun.probe", die)

    assert watch.poll_once("https://youtu.be/abc", deque(maxlen=20), cfg, FakeClient()) is False
    assert spawned == []
    out = capsys.readouterr().out
    assert "timed out" in out
