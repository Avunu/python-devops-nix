"""process-compose's REST API over its unix socket, as devenv starts it.

devenv's process-compose manager listens on $PC_SOCKET_PATH
(process.managers.process-compose.unixSocket), and that is the only
connection the TUI makes: the process table, the logs, and starting, stopping
and restarting one process or the whole project. The routes are
process-compose's (src/api/routes.go): stable across the releases devenv has
shipped, and read here leniently, so a field a release adds or drops is not an
error.

Standard library only: http.client over AF_UNIX, one short request at a time,
polled by the caller.
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import urllib.parse
from dataclasses import dataclass
from typing import Any

#: process-compose's statuses (src/types/process.go) for a process that is up,
#: and for one on its way there.
UP = frozenset({"Running", "Launched"})
STARTING = frozenset({"Pending", "Launching", "Restarting", "Scheduled"})


class Error(Exception):
    """A request process-compose answered with an error."""


class Unavailable(Error):
    """Nothing is listening on the socket: the stack is not running."""


@dataclass(frozen=True)
class Process:
    name: str
    status: str
    ready: str = ""
    has_probe: bool = False
    running: bool = False
    restarts: int = 0
    exit_code: int = 0
    pid: int = 0
    namespace: str = ""
    age_ns: int = 0

    @property
    def state(self) -> str:
        """One word for the table: up (running, and ready if it has a probe),
        starting, done (a one-shot that exited 0), failed, or down."""
        if self.running:
            return "up" if not self.has_probe or self.ready == "Ready" else "starting"
        if self.status in STARTING:
            return "starting"
        if self.status == "Completed":
            return "done" if self.exit_code == 0 else "failed"
        if self.status == "Error":
            return "failed"
        return "down"


def _process(row: dict[str, Any]) -> Process:
    def int_(key: str) -> int:
        value = row.get(key)
        return value if isinstance(value, int) else 0

    status = str(row.get("status", ""))
    namespace = row.get("namespace", "")
    return Process(
        name=str(row.get("name", "")),
        status=status,
        ready=str(row.get("is_ready", "") or ""),
        has_probe=bool(row.get("has_ready_probe", False)),
        running=bool(row.get("is_running", status in UP)),
        restarts=int_("restarts"),
        exit_code=int_("exit_code"),
        pid=int_("pid"),
        namespace=namespace if isinstance(namespace, str) else "",
        age_ns=int_("age"),
    )


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float) -> None:
        super().__init__("localhost", timeout=timeout)
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self._path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


class Client:
    """process-compose at `path` (default $PC_SOCKET_PATH)."""

    def __init__(self, path: str | None = None, timeout: float = 3.0) -> None:
        self.path = path or os.environ.get("PC_SOCKET_PATH", "")
        self.timeout = timeout

    def _request(self, method: str, route: str) -> Any:
        if not self.path:
            raise Unavailable("no process-compose socket ($PC_SOCKET_PATH is not set)")
        conn = _UnixConnection(self.path, self.timeout)
        try:
            conn.request(method, route, headers={"Accept": "application/json"})
            response = conn.getresponse()
            body = response.read()
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            raise Unavailable(f"nothing is listening on {self.path}") from exc
        except OSError as exc:
            raise Error(f"{method} {route}: {exc}") from exc
        finally:
            conn.close()
        try:
            data = json.loads(body) if body else None
        except ValueError:
            data = body.decode(errors="replace")
        if response.status >= 400:
            message = data.get("error") if isinstance(data, dict) else data
            raise Error(
                f"{method} {route}: {response.status} {message or response.reason}"
            )
        return data

    @staticmethod
    def _name(name: str) -> str:
        return urllib.parse.quote(name, safe="")

    def alive(self) -> bool:
        try:
            self._request("GET", "/live")
        except Error:
            return False
        return True

    def processes(self) -> list[Process]:
        data = self._request("GET", "/processes")
        rows = data.get("data", []) if isinstance(data, dict) else []
        return sorted(
            (_process(row) for row in rows if isinstance(row, dict)),
            key=lambda p: p.name,
        )

    def project(self) -> dict[str, Any]:
        data = self._request("GET", "/project/state")
        return data if isinstance(data, dict) else {}

    def logs(self, name: str, limit: int = 500) -> list[str]:
        """The last `limit` lines of the process's log."""
        data = self._request("GET", f"/process/logs/{self._name(name)}/0/{limit}")
        logs = data.get("logs", []) if isinstance(data, dict) else []
        return [str(line) for line in logs or []]

    def start(self, name: str) -> None:
        self._request("POST", f"/process/start/{self._name(name)}")

    def stop(self, name: str) -> None:
        self._request("PATCH", f"/process/stop/{self._name(name)}")

    def restart(self, name: str) -> None:
        self._request("POST", f"/process/restart/{self._name(name)}")

    def shutdown(self) -> None:
        """Stop every process and process-compose itself."""
        try:
            self._request("POST", "/project/stop")
        except Error as exc:
            # The server may close the connection on its way down.
            dropped = (ConnectionResetError, http.client.RemoteDisconnected)
            if not isinstance(exc.__cause__, dropped) and self.alive():
                raise
