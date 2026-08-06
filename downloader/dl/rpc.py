import itertools
import json
import urllib.error
import urllib.request


class Aria2Error(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"aria2 error {code}: {message}")
        self.code = code
        self.message = message


class Aria2Unreachable(Exception):
    pass


class Aria2:
    def __init__(self, host: str, port: int, secret: str, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.secret = secret
        self.timeout = timeout
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
            raise Aria2Unreachable(f"HTTP {exc.code} from {self.url}") from exc
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            raise Aria2Unreachable(str(exc)) from exc
        if "error" in body:
            err = body["error"]
            raise Aria2Error(int(err.get("code", -1)), str(err.get("message", "")))
        return body.get("result")

    def get_version(self) -> dict:
        return self._call("aria2.getVersion")

    def add_uri(self, uris: list[str], options: dict) -> str:
        return self._call("aria2.addUri", uris, options)

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
