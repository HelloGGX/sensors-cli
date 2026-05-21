"""Unix domain socket control plane for the running sensors (RPC to asyncio events)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

from sensors.tui.display import DisplayEvents


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


def write_control_file(
    control_path: Path,
    socket_path: Path,
    pid: int,
    config_file_name: str,
    working_dir: str | None = None,
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


async def rpc_unix(
    socket_path: Path,
    method: str,
    *,
    params: dict[str, Any] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Send one JSON-line request and read one JSON-line response."""
    path = str(socket_path.resolve())
    reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), timeout=timeout)
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


def rpc_unix_sync(socket_path: Path, method: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Synchronous wrapper for CLI code."""
    return asyncio.run(rpc_unix(socket_path, method, timeout=timeout))


async def start_control_server(
    socket_path: Path,
    events: DisplayEvents,
) -> asyncio.AbstractServer:
    """Bind Unix socket and listen for RPC connections."""
    socket_path = Path(socket_path).resolve()
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    async def _client_cb(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await handle_control_connection(reader, writer, events)

    return await asyncio.start_unix_server(_client_cb, path=str(socket_path))


async def ensure_no_live_sensors(control_path: Path, socket_path: Path) -> None:
    """Raise RuntimeError if another sensors instance appears to own this config."""
    data = control_state(control_path)
    if not data:
        return
    pid = data.get("pid")
    sock = data.get("socketPath")
    if isinstance(pid, int) and _pid_alive(pid) and sock:
        sp = Path(sock)
        if sp.exists():
            try:
                res = await rpc_unix(sp, "ping", timeout=2.0)
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
    sock = data.get("socketPath")
    if isinstance(pid, int) and _pid_alive(pid) and sock:
        sp = Path(sock)
        if sp.exists():
            try:
                res = rpc_unix_sync(sp, "ping", timeout=2.0)
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
    sock = data.get("socketPath")
    if not isinstance(pid, int) or not sock:
        return False
    if not _pid_alive(pid):
        return False
    sp = Path(sock)
    if not sp.exists():
        return False
    try:
        res = rpc_unix_sync(sp, "ping", timeout=2.0)
        return bool(res.get("ok"))
    except (OSError, TimeoutError, json.JSONDecodeError):
        return False


def stop_running_sensors(
    control_path: Path,
    socket_path: Path,
    *,
    wait: bool = True,
    timeout_sec: float = 15.0,
) -> tuple[str, int]:
    """Send SIGTERM to the PID in control.json (same as interactive Ctrl+C).

    Removes stale control/socket files if the process is already gone.
    Returns (message, exit_code) where exit_code is 0 on success or idempotent no-op,
    and 1 if the process was signalled but did not exit within timeout_sec (when wait=True).
    """
    data = control_state(control_path)
    if not data:
        return ("No sensors is running.", 0)

    pid = data.get("pid")
    sock_from_file = data.get("socketPath")
    sp = Path(sock_from_file) if isinstance(sock_from_file, str) else socket_path

    if not isinstance(pid, int):
        remove_control_artifacts(control_path, socket_path)
        return ("Removed invalid control file.", 0)

    if not _pid_alive(pid):
        remove_control_artifacts(control_path, sp)
        return (f"Removed stale control metadata (PID {pid} was not running).", 0)

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        remove_control_artifacts(control_path, sp)
        return (f"Process {pid} already exited; cleaned up metadata.", 0)

    if not wait:
        return (f"Sent SIGTERM to sensors (PID {pid}).", 0)

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return ("Sensors stopped.", 0)
        time.sleep(0.1)

    return (
        f"Sensors (PID {pid}) did not exit within {timeout_sec:.0f}s after SIGTERM. "
        f"Try: kill -KILL {pid}",
        1,
    )
