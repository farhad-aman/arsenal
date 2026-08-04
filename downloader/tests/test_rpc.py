import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from dl.rpc import Aria2, Aria2Error, Aria2Unreachable

SECRET = "s3cr3t"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.server.calls.append(body)
        token = body["params"][0] if body["params"] else None
        if token != f"token:{SECRET}":
            payload = {"id": body["id"], "error": {"code": 1, "message": "Unauthorized"}}
        else:
            payload = {"id": body["id"], "result": self.server.replies.get(body["method"], "OK")}
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), Handler)
    srv.calls = []
    srv.replies = {}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()


@pytest.fixture
def client(server):
    return Aria2("127.0.0.1", server.server_address[1], SECRET)


def test_get_version(server, client):
    server.replies["aria2.getVersion"] = {"version": "1.37.0"}
    assert client.get_version()["version"] == "1.37.0"


def test_secret_is_sent_as_first_param(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    assert server.calls[0]["params"][0] == f"token:{SECRET}"


def test_jsonrpc_envelope_is_well_formed(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    call = server.calls[0]
    assert call["jsonrpc"] == "2.0"
    assert call["method"] == "aria2.getVersion"
    assert call["id"]


def test_add_uri_passes_uris_and_options(server, client):
    server.replies["aria2.addUri"] = "gid123"
    gid = client.add_uri(["https://e.com/a.iso"], {"dir": "/tmp"})
    assert gid == "gid123"
    assert server.calls[0]["params"][1] == ["https://e.com/a.iso"]
    assert server.calls[0]["params"][2] == {"dir": "/tmp"}


def test_tell_active_sends_no_extra_params(server, client):
    server.replies["aria2.tellActive"] = []
    client.tell_active()
    assert len(server.calls[0]["params"]) == 1


def test_tell_waiting_sends_offset_and_num(server, client):
    server.replies["aria2.tellWaiting"] = []
    client.tell_waiting()
    assert server.calls[0]["params"][1:] == [0, 1000]


def test_tell_stopped_sends_offset_and_num(server, client):
    server.replies["aria2.tellStopped"] = []
    client.tell_stopped()
    assert server.calls[0]["params"][1:] == [0, 1000]


def test_change_position_params(server, client):
    server.replies["aria2.changePosition"] = 2
    assert client.change_position("g1", -1, "POS_CUR") == 2
    assert server.calls[0]["params"][1:] == ["g1", -1, "POS_CUR"]


def test_change_global_option_params(server, client):
    server.replies["aria2.changeGlobalOption"] = "OK"
    client.change_global_option({"max-overall-download-limit": "2M"})
    assert server.calls[0]["params"][1] == {"max-overall-download-limit": "2M"}


def test_pause_unpause_remove_send_gid(server, client):
    for method, name in [
        ("aria2.pause", "pause"),
        ("aria2.unpause", "unpause"),
        ("aria2.remove", "remove"),
    ]:
        server.calls.clear()
        server.replies[method] = "g1"
        getattr(client, name)("g1")
        assert server.calls[0]["params"][1] == "g1"


def test_rpc_fault_raises_aria2error(server, client):
    bad = Aria2("127.0.0.1", server.server_address[1], "wrong-secret")
    with pytest.raises(Aria2Error) as exc:
        bad.get_version()
    assert exc.value.code == 1
    assert "Unauthorized" in exc.value.message


def test_connection_refused_raises_unreachable():
    dead = Aria2("127.0.0.1", 1, SECRET, timeout=0.5)
    with pytest.raises(Aria2Unreachable):
        dead.get_version()


def test_ids_are_unique_across_calls(server, client):
    server.replies["aria2.getVersion"] = {}
    client.get_version()
    client.get_version()
    assert server.calls[0]["id"] != server.calls[1]["id"]
