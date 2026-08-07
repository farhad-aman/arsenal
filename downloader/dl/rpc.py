import itertools
import json
import urllib.error
import urllib.request
from pathlib import Path


class Aria2Error(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"aria2 error {code}: {message}")
        self.code = code
        self.message = message


class Aria2Unreachable(Exception):
    pass


def _as_error(body: object) -> Aria2Error | None:
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if not isinstance(err, dict):
        return None
    try:
        code = int(err.get("code", -1))
    except (TypeError, ValueError):
        code = -1
    return Aria2Error(code, str(err.get("message", "")))


def _refusal(exc: urllib.error.HTTPError) -> Aria2Error | None:
    """The aria2 rejection carried by an HTTP error response, if there is one."""
    try:
        return _as_error(json.loads(exc.read()))
    except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
        return None


class Aria2:
    def __init__(
        self,
        host: str,
        port: int,
        secret: str,
        timeout: float = 5.0,
        state: Path | None = None,
    ):
        self.host = host
        self.port = port
        self.secret = secret
        self.timeout = timeout
        # Where to note a new download's queue time. aria2 never reports one,
        # and addUri is the only moment the gid and the clock are both in hand.
        self.state = state
        self._ids = itertools.count(1)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/jsonrpc"

    def _call(self, method: str, *params):
        payload = {
            "jsonrpc": "2.0",
            "id": f"dl-{next(self._ids)}",
            "method": method,
            "params": [f"token:{self.secret}", *params],
        }
        request = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # aria2 refuses a call with 400 and a JSON-RPC error body. Judging by
            # the status code alone threw that message away and reported a
            # working daemon as lost.
            refusal = _refusal(exc)
            if refusal is not None:
                raise refusal from exc
            raise Aria2Unreachable(f"HTTP {exc.code} from {self.url}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise Aria2Unreachable(str(exc)) from exc
        failure = _as_error(body)
        if failure is not None:
            raise failure
        return body.get("result")

    def get_version(self) -> dict:
        return self._call("aria2.getVersion")

    def add_uri(self, uris: list[str], options: dict) -> str:
        return self._added(self._call("aria2.addUri", uris, options))

    def add_torrent(self, path: Path, options: dict) -> str:
        """Hand a .torrent from disk to the daemon.

        addUri cannot: it takes somewhere to fetch from, and this is already
        here. A .torrent behind a URL needs none of this — aria2 downloads it
        and follows it into the transfer itself.
        """
        from . import torrent

        return self._added(
            self._call("aria2.addTorrent", torrent.encoded(path), [], options)
        )

    def _added(self, gid: str) -> str:
        if self.state is not None:
            from . import started

            try:
                started.record(self.state, gid)
            except OSError:
                # Losing a timestamp costs a column; failing the add costs the
                # download.
                pass
        return gid

    def tell_active(self) -> list[dict]:
        return self._call("aria2.tellActive")

    def tell_waiting(self, offset: int = 0, num: int = 1000) -> list[dict]:
        return self._call("aria2.tellWaiting", offset, num)

    def tell_stopped(self, offset: int = 0, num: int = 1000) -> list[dict]:
        return self._call("aria2.tellStopped", offset, num)

    def tell_status(self, gid: str) -> dict:
        return self._call("aria2.tellStatus", gid)

    def pause(self, gid: str) -> str:
        return self._call("aria2.pause", gid)

    def unpause(self, gid: str) -> str:
        return self._call("aria2.unpause", gid)

    def remove(self, gid: str) -> str:
        return self._call("aria2.remove", gid)

    def remove_download_result(self, gid: str) -> str:
        """Forget a stopped download.

        remove() only moves it to the stopped list, where it stays for the
        life of the daemon.
        """
        return self._call("aria2.removeDownloadResult", gid)

    def change_position(self, gid: str, pos: int, how: str) -> int:
        return self._call("aria2.changePosition", gid, pos, how)

    def get_option(self, gid: str) -> dict:
        return self._call("aria2.getOption", gid)

    def change_option(self, gid: str, options: dict) -> str:
        return self._call("aria2.changeOption", gid, options)

    def change_global_option(self, options: dict) -> str:
        return self._call("aria2.changeGlobalOption", options)

    def get_global_stat(self) -> dict:
        return self._call("aria2.getGlobalStat")

    def shutdown(self) -> str:
        return self._call("aria2.shutdown")
