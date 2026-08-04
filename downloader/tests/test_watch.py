from collections import deque

import pytest

from dl import watch


@pytest.fixture
def cfg(sandbox_cfg):
    return sandbox_cfg


class FakeClient:
    def __init__(self):
        self.added = []

    def add_uri(self, uris, options):
        self.added.append(uris[0])
        return "gid"


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
