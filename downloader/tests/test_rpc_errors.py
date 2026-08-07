"""A rejected call is not a lost daemon.

aria2 answers an operation it refuses with HTTP 400 and a JSON-RPC error body.
Deciding on the status code alone threw the message away and told the user the
daemon had gone.
"""

import io
import json
import urllib.error

import pytest

from dl.rpc import Aria2, Aria2Error, Aria2Unreachable


def http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "http://127.0.0.1:6800/jsonrpc", code, "Bad Request", {}, io.BytesIO(body)
    )


def raising(exc):
    def opener(request, timeout=None):
        raise exc

    return opener


def rpc_error(code: int, message: str) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": "dl-1", "error": {"code": code, "message": message}}).encode()


@pytest.fixture
def client():
    return Aria2("127.0.0.1", 6800, "secret")


def test_a_rejected_call_raises_a_readable_aria2_error(client, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", raising(http_error(400, rpc_error(1, "No such download")))
    )
    with pytest.raises(Aria2Error) as caught:
        client.pause("0000000000000000")
    assert caught.value.code == 1
    assert "No such download" in caught.value.message


def test_a_rejected_call_is_not_reported_as_unreachable(client, monkeypatch):
    monkeypatch.setattr(
        "urllib.request.urlopen", raising(http_error(400, rpc_error(1, "No such download")))
    )
    with pytest.raises(Aria2Error):
        client.pause("0000000000000000")


def test_an_http_error_with_no_json_body_is_still_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(502, b"<html>gateway</html>")))
    with pytest.raises(Aria2Unreachable) as caught:
        client.get_version()
    assert "502" in str(caught.value)


def test_an_http_error_with_json_but_no_error_key_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(500, b'{"result": "odd"}')))
    with pytest.raises(Aria2Unreachable):
        client.get_version()


def test_an_http_error_with_an_empty_body_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(400, b"")))
    with pytest.raises(Aria2Unreachable):
        client.get_version()


def test_a_non_numeric_error_code_does_not_crash_the_mapping(client, monkeypatch):
    body = json.dumps({"error": {"code": "weird", "message": "odd"}}).encode()
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(400, body)))
    with pytest.raises(Aria2Error) as caught:
        client.get_version()
    assert "odd" in caught.value.message


def test_an_error_body_that_is_not_an_object_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(400, b'{"error": "flat"}')))
    with pytest.raises(Aria2Unreachable):
        client.get_version()


def test_a_body_that_is_a_bare_list_is_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(http_error(400, b"[1, 2, 3]")))
    with pytest.raises(Aria2Unreachable):
        client.get_version()


def test_a_dead_socket_is_still_unreachable(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", raising(urllib.error.URLError("refused")))
    with pytest.raises(Aria2Unreachable):
        client.get_version()


def test_a_two_hundred_with_an_error_body_still_raises_aria2_error(client, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return rpc_error(1, "Unauthorized")

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: Response())
    with pytest.raises(Aria2Error) as caught:
        client.get_version()
    assert caught.value.code == 1


def test_a_successful_call_still_returns_its_result(client, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"result": {"version": "1.37.0"}}).encode()

    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: Response())
    assert client.get_version() == {"version": "1.37.0"}
