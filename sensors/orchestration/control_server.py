"""Control plane for the running sensors (RPC to asyncio events).

POSIX: Unix domain socket at ``.sensors/<config>.sock``.
Windows: asyncio has no Unix socket support, so a TCP server bound to
127.0.0.1 on an ephemeral port is used instead; the port is recorded in
the control file alongside the planned socket path.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from sensors.events import DisplayEvents


async def handle_control_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    events: DisplayEvents,
) -> None:
    """Handle one JSON-line RPC: {"method":"ping"|"snapshot"} -> {"ok":true} or error."""
    try:
        raw = await reader.readline()
        if not raw:
            return
        msg = json.loads(raw.decode())
        method = msg.get("method")
        if method == "ping":
            response: dict[str, Any] = {"ok": True}
        elif method == "snapshot":
            events.snapshot.set()
            response = {"ok": True}
        elif method == "clear":
            events.clear.set()
            response = {"ok": True}
        elif method == "shutdown":
            events.shutdown.set()
            response = {"ok": True}
        elif method == "rerun":
            name = msg.get("name", "")
            ev = events.rerun_events.get(name)
            if ev is not None:
                ev.set()
                response = {"ok": True}
            else:
                response = {"ok": False, "error": f"runner {name!r} not found or not rerunnable"}
        else:
            response = {"ok": False, "error": f"unknown method: {method!r}"}
    except Exception as e:
        response = {"ok": False, "error": str(e)}
    try:
        writer.write((json.dumps(response) + "\n").encode())
        await writer.drain()
    finally:
        writer.close()
        await writer.wait_closed()


def write_control_file(  # noqa: PLR0913 -- endpoint metadata (path + port) is simplest as flat args
    control_path: Path,
    socket_path: Path,
    pid: int,
    config_file_name: str,
    working_dir: str | None = None,
    *,
    port: int | None = None,
) -> None:
    """Write metadata so clients can find the listener."""
    control_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "socketPath": str(socket_path.resolve()),
        "pid": pid,
        "config": config_file_name,
    }
    if working_dir is not None:
        payload["workingDir"] = working_dir
    if port is not None:
        payload["port"] = port
    control_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def remove_control_artifacts(control_path: Path, socket_path: Path) -> None:
    """Best-effort cleanup of socket file and control JSON."""
    try:
        if socket_path.exists():
            socket_path.unlink()
    except OSError:
        pass
    try:
        if control_path.exists():
            control_path.unlink()
    except OSError:
        pass


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID exists.

    Windows note: ``os.kill(pid, 0)`` maps to TerminateProcess there and would
    actually kill the process, so use the Win32 API instead.
    """
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            # ERROR_ACCESS_DENIED (5): process exists but is off-limits -> alive
            return ctypes.get_last_error() == 5
        try:
            exit_code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def control_state(control_path: Path) -> dict[str, Any] | None:
    """Return parsed control.json or None if missing/unreadable."""
    if not control_path.exists():
        return None
    try:
        return json.loads(control_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _endpoint_from_state(data: dict[str, Any]) -> tuple[Path | None, int | None]:
    """Extract the RPC endpoint from control state: (unix path, tcp port)."""
    port = data.get("port")
    if isinstance(port, int) and 0 < port < 65536:
        return None, port
    sock = data.get("socketPath")
    return (Path(sock) if isinstance(sock, str) else None), None


async def rpc(
    socket_path: Path | None,
    method: str,
    *,
    port: int | None = None,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Send one JSON-line request and read one JSON-line response.

    Connects via TCP when ``port`` is given (Windows transport), else via the
    Unix domain socket at ``socket_path``.
    """
    if port is not None:
        connect = asyncio.open_connection("127.0.0.1", port)
    else:
        if socket_path is None:
            return {"ok": False, "error": "no control endpoint"}
        connect = asyncio.open_unix_connection(str(Path(socket_path).resolve()))
    reader, writer = await asyncio.wait_for(connect, timeout=timeout)
    payload = {"method": method, **(params or {})}
    try:
        writer.write((json.dumps(payload) + "\n").encode())
        await writer.drain()
        raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
        if not raw:
            return {"ok": False, "error": "empty response"}
        return json.loads(raw.decode())
    finally:
        writer.close()
        await writer.wait_closed()


def rpc_sync(
    socket_path: Path | None,
    method: str,
    *,
    port: int | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Synchronous wrapper for CLI code."""
    return asyncio.run(rpc(socket_path, method, port=port, timeout=timeout))


async def start_control_server(
    socket_path: Path,
    events: DisplayEvents,
) -> tuple[asyncio.AbstractServer, int | None]:
    """Listen for RPC connections.

    Returns ``(server, port)`` where ``port`` is the bound TCP port on Windows
    and ``None`` on POSIX (Unix socket at ``socket_path``).
    """
    async def _client_cb(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await handle_control_connection(reader, writer, events)

    if os.name == "nt":
        server = await asyncio.start_server(_client_cb, host="127.0.0.1", port=0)
        return server, server.sockets[0].getsockname()[1]

    socket_path = Path(socket_path).resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()
    server = await asyncio.start_unix_server(_client_cb, path=str(socket_path))
    return server, None


async def ensure_no_live_sensors(control_path: Path, socket_path: Path) -> None:
    """Raise RuntimeError if another sensors instance appears to own this config."""
    data = control_state(control_path)
    if not data:
        return
    pid = data.get("pid")
    sock, port = _endpoint_from_state(data)
    if isinstance(pid, int) and _pid_alive(pid) and (port is not None or (sock and sock.exists())):
        try:
            res = await rpc(sock, "ping", port=port, timeout=2.0)
            if res.get("ok"):
                raise RuntimeError(
                    "A sensors instance is already running for this project. "
                )
        except RuntimeError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError, ConnectionError):
            # Stale: ping failed — allow start
            pass


def ensure_no_live_sensors_sync(control_path: Path, socket_path: Path) -> None:
    """Synchronous wrapper — only call from outside an event loop."""
    data = control_state(control_path)
    if not data:
        return
    pid = data.get("pid")
    sock, port = _endpoint_from_state(data)
    if isinstance(pid, int) and _pid_alive(pid) and (port is not None or (sock and sock.exists())):
        try:
            res = rpc_sync(sock, "ping", port=port, timeout=2.0)
            if res.get("ok"):
                raise RuntimeError(
                    "A sensors instance is already running for this project. "
                )
        except RuntimeError:
            raise
        except (OSError, TimeoutError, json.JSONDecodeError, ConnectionError):
            pass


def is_sensors_running(control_path: Path, socket_path: Path) -> bool:
    """Return True if control file exists, PID alive, and ping succeeds."""
    data = control_state(control_path)
    if not data:
        return False
    pid = data.get("pid")
    if not isinstance(pid, int):
        return False
    if not _pid_alive(pid):
        return False
    sock, port = _endpoint_from_state(data)
    if port is None and (sock is None or not sock.exists()):
        return False
    try:
        res = rpc_sync(sock, "ping", port=port, timeout=2.0)
        return bool(res.get("ok"))
    except (OSError, TimeoutError, json.JSONDecodeError):
        return False


def _request_graceful_shutdown(data: dict[str, Any]) -> bool:
    """Ask the worker to shut itself down via its control channel. True on ack."""
    sock, port = _endpoint_from_state(data)
    if port is None and (sock is None or not sock.exists()):
        return False
    try:
        res = rpc_sync(sock, "shutdown", port=port, timeout=2.0)
    except (OSError, TimeoutError, json.JSONDecodeError, ConnectionError):
        return False
    return bool(res.get("ok"))


def stop_running_sensors(
    control_path: Path,
    socket_path: Path,
    *,
    wait: bool = True,
    timeout_sec: float = 15.0,
) -> tuple[str, int]:
    """Stop the sensors that owns control.json.

    Prefers the graceful RPC ``shutdown`` (lets the worker clean up); falls back
    to SIGTERM on POSIX and TerminateProcess via os.kill on Windows. Removes
    stale control/socket files if the process is already gone.
    Returns (message, exit_code) where exit_code is 0 on success or idempotent no-op,
    and 1 if the process was signalled but did not exit within timeout_sec (when wait=True).
    """
    data = control_state(control_path)
    if not data:
        return ("No sensors is running.", 0)

    pid = data.get("pid")
    sock, _port = _endpoint_from_state(data)
    if sock is None:
        sock = socket_path

    if not isinstance(pid, int):
        remove_control_artifacts(control_path, socket_path)
        return ("Removed invalid control file.", 0)

    if not _pid_alive(pid):
        remove_control_artifacts(control_path, sock)
        return (f"Removed stale control metadata (PID {pid} was not running).", 0)

    if not _request_graceful_shutdown(data):
        try:
            os.kill(pid, signal.SIGTERM)  # POSIX: graceful; Windows: forceful
        except (ProcessLookupError, PermissionError, OSError):
            remove_control_artifacts(control_path, sock)
            return (f"Process {pid} already exited; cleaned up metadata.", 0)

    if not wait:
        return (f"Stop requested for sensors (PID {pid}).", 0)

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return ("Sensors stopped.", 0)
        time.sleep(0.1)

    hint = f"kill -KILL {pid}" if os.name != "nt" else f"taskkill /F /PID {pid}"
    return (
        f"Sensors (PID {pid}) did not exit within {timeout_sec:.0f}s after stop request. "
        f"Try: {hint}",
        1,
    )
