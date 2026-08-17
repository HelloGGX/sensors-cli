"""End-to-end subprocess tests for sensors CLI (Unix socket on POSIX, TCP on Windows)."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from sensors import __version__
from sensors.config.loader import (
    control_path_for_config,
    resolve_config_path,
    socket_path_for_config,
)
from sensors.orchestration.control_server import (
    _endpoint_from_state,
    _pid_alive,
    control_state,
    rpc_sync,
    stop_running_sensors,
)


def _sensors_cli() -> list[str]:
    return [sys.executable, "-m", "sensors.cli"]


def test_sensors_cli_version() -> None:
    st = subprocess.run(
        _sensors_cli() + ["--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 0
    assert st.stdout.strip() == f"sensors {__version__}"


def _write_minimal_project(project: Path) -> None:
    sensors = project / ".sensors"
    sensors.mkdir(parents=True)
    cfg = sensors / "e2e.sensors.yaml"
    py = sys.executable.replace("\\", "\\\\")
    cfg.write_text(
        "\n".join(
            [
                "version: 1",
                "runners:",
                "  - name: smoke",
                "    parser: pytest",
                "    mode: interval",
                "    interval: 200",
                f"    command: {py} -c \"print('1 passed')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _wait_until(pred, timeout: float = 8.0, interval: float = 0.05) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return
        time.sleep(interval)
    raise AssertionError("condition not met within timeout")


def _wait_pid_exit(pid: int, timeout: float = 5.0) -> None:
    """Block until *pid* is no longer running (best-effort, no error on timeout)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)


def _kill_pid(pid: int) -> None:
    """Stop signal + wait for a single PID (cross-platform)."""
    with contextlib.suppress(OSError):
        os.kill(pid, signal.SIGTERM)
    _wait_pid_exit(pid)


def _find_sensors_pids_for_dir(project: Path) -> set[int]:
    """Find sensors worker PIDs whose argv references *project*, via the process table.

    This works even after control files have been removed by ``sensors stop``.
    """
    from sensors.orchestration.sensors_processes import list_sensors_start_processes

    resolved = str(project.resolve())
    return {p.pid for p in list_sensors_start_processes() if p.folder and resolved in p.folder}


@pytest.fixture
def project_dir():
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        _write_minimal_project(project)
        yield project
        # Stop any sensors workers for this temp dir, even if control files are gone.
        try:
            cfg_path = resolve_config_path(str(project), None)
            stop_running_sensors(control_path_for_config(cfg_path), socket_path_for_config(cfg_path))
        except Exception:
            pass
        for pid in _find_sensors_pids_for_dir(project):
            _kill_pid(pid)
        # Windows: runner grandchildren orphaned by a forceful worker kill can
        # hold the project dir as their CWD briefly; retry deletion until free.
        import shutil

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            shutil.rmtree(project.parent, ignore_errors=True)
            if not project.parent.exists():
                break
            time.sleep(0.1)


def test_run_background_writes_control_and_state(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stderr

    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)

    data = control_state(ctl)
    assert data is not None
    assert "socketPath" in data and "pid" in data
    sock, port = _endpoint_from_state(data)
    assert port is not None or sock.exists()  # POSIX: socket file; Windows: TCP port

    # Runner should eventually populate state
    state_file = cfg_path.parent / "e2e.state.json"
    _wait_until(lambda: state_file.exists() and state_file.stat().st_size > 10)

    _kill_pid(int(data["pid"]))


def test_rpc_snapshot_persists_snapshot_block(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0, cp.stderr

    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)
    data = control_state(ctl)
    assert data is not None
    sock, port = _endpoint_from_state(data)

    res = rpc_sync(sock, "snapshot", port=port, timeout=5.0)
    assert res.get("ok") is True

    state_file = cfg_path.parent / "e2e.state.json"
    _wait_until(
        lambda: state_file.exists()
        and b"snapshot" in state_file.read_bytes()
        and b'"timestamp"' in state_file.read_bytes()
    )

    _kill_pid(int(data["pid"]))


def test_second_run_fails_when_sensors_running(project_dir: Path) -> None:
    cp1 = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp1.returncode == 0, cp1.stderr

    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists())

    cp2 = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp2.returncode == 1
    assert "already running" in (cp2.stderr or "").lower() or "already running" in (
        cp2.stdout or ""
    ).lower()

    data = control_state(ctl)
    if data and "pid" in data:
        _kill_pid(int(data["pid"]))


def test_snapshot_cli_after_background_run(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)

    sp = subprocess.run(
        _sensors_cli() + ["snapshot", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sp.returncode == 0
    assert "snapshot" in sp.stdout.lower()

    data = control_state(ctl)
    if data and "pid" in data:
        _kill_pid(int(data["pid"]))


def test_snapshot_cli_fails_without_running_sensors(project_dir: Path) -> None:
    sp = subprocess.run(
        _sensors_cli() + ["snapshot", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sp.returncode == 2
    out = (sp.stdout or "") + (sp.stderr or "")
    assert "no sensors" in out.lower()


def test_show_fails_without_running_sensors(project_dir: Path) -> None:
    sp = subprocess.run(
        _sensors_cli() + ["show", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sp.returncode == 2
    out = (sp.stdout or "") + (sp.stderr or "")
    assert "no sensors" in out.lower()


def test_status_requires_project_or_all() -> None:
    st = subprocess.run(
        _sensors_cli() + ["status"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 2
    out = (st.stdout or "") + (st.stderr or "")
    assert "required" in out.lower() or "--all" in out


@pytest.mark.skipif(sys.platform == "win32", reason="`sensors status --all` is not supported on Windows")
def test_status_all_smoke() -> None:
    st = subprocess.run(
        _sensors_cli() + ["status", "--all"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert st.returncode == 0
    out = st.stdout or ""
    assert "Sensors - start processes:" in out or "No sensors start" in out


def test_status_not_running_exits_1(project_dir: Path) -> None:
    st = subprocess.run(
        _sensors_cli() + ["status", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 1
    out = (st.stdout or "") + (st.stderr or "")
    assert "not running" in out.lower()


def test_status_running_after_background_run(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)
    data = control_state(ctl)
    assert data is not None
    pid = str(data["pid"])

    st = subprocess.run(
        _sensors_cli() + ["status", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 0
    out = (st.stdout or "") + (st.stderr or "")
    assert "running" in out.lower()
    assert pid in out

    subprocess.run(
        _sensors_cli() + ["stop", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    _wait_until(lambda: not ctl.exists(), timeout=10.0)


@pytest.mark.skipif(sys.platform == "win32", reason="`sensors status --all` is not supported on Windows")
def test_status_all_lists_background_worker(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)
    data = control_state(ctl)
    assert data is not None
    pid = int(data["pid"])

    sa = subprocess.run(
        _sensors_cli() + ["status", "--all"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert sa.returncode == 0
    out = sa.stdout or ""
    assert str(pid) in out
    # Temp dirs get compact "(temp) tmpXYZ/proj" display; verify the dir name appears.
    dir_name = project_dir.name  # "proj"
    parent_name = project_dir.parent.name  # "tmpXYZ..."
    assert dir_name in out, (
        f"status --all must show the project dir name "
        f"(expected {dir_name!r} somewhere in output):\n{out}"
    )
    assert parent_name in out, (
        f"status --all must show the temp parent dir name "
        f"(expected {parent_name!r} somewhere in output):\n{out}"
    )

    subprocess.run(
        _sensors_cli() + ["stop", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    _wait_until(lambda: not ctl.exists(), timeout=10.0)


def _write_on_check_project(project: Path) -> None:
    """Project with one on_check runner that emits a fixed string."""
    sensors = project / ".sensors"
    sensors.mkdir(parents=True)
    cfg = sensors / "onc.sensors.yaml"
    cfg.write_text(
        "\n".join(
            [
                "version: 1",
                "runners:",
                "  - name: versions",
                "    mode: on_check",
                f"    command: {sys.executable.replace(chr(92), chr(92) * 2)} -c \"print('node 20.0.0'); print('python 3.12')\"",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_on_check_runner_runs_command_on_check() -> None:
    """`sensors check` executes on_check runner's command and embeds its raw output."""
    with tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "proj"
        project.mkdir()
        _write_on_check_project(project)

        st = subprocess.run(
            _sensors_cli() + ["check", str(project)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert st.returncode == 0, (st.stdout, st.stderr)
        assert "versions" in st.stdout
        assert "on_check" in st.stdout
        assert "node 20.0.0" in st.stdout
        assert "python 3.12" in st.stdout


def test_check_no_state_exits_2(project_dir: Path) -> None:
    st = subprocess.run(
        _sensors_cli() + ["check", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 2


def test_sensors_cli_entry_status_not_running(project_dir: Path) -> None:
    st = subprocess.run(
        [sys.executable, "-m", "sensors.cli", "status", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert st.returncode == 1


def test_stop_when_nothing_running(project_dir: Path) -> None:
    sp = subprocess.run(
        _sensors_cli() + ["stop", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sp.returncode == 0
    assert "no sensors" in sp.stdout.lower()


def test_stop_after_background_run(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)
    data = control_state(ctl)
    assert data is not None
    pid = int(data["pid"])

    sp = subprocess.run(
        _sensors_cli() + ["stop", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert sp.returncode == 0
    assert "stopped" in sp.stdout.lower()

    _wait_until(lambda: not ctl.exists(), timeout=10.0)
    assert not _pid_alive(pid)


def test_ping_rpc(project_dir: Path) -> None:
    cp = subprocess.run(
        _sensors_cli() + ["start", str(project_dir)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert cp.returncode == 0
    cfg_path = resolve_config_path(str(project_dir), None)
    ctl = control_path_for_config(cfg_path)
    _wait_until(lambda: ctl.exists() and control_state(ctl) is not None)
    data = control_state(ctl)
    assert data is not None
    sock, port = _endpoint_from_state(data)
    res = rpc_sync(sock, "ping", port=port)
    assert res.get("ok") is True
    _kill_pid(int(data["pid"]))
