"""Unit tests for sensors process argv parsing."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from sensors.orchestration.sensors_processes import (
    compact_temp_path,
    format_folder_for_display,
    looks_like_macos_temp_sensors_dir,
    normalize_path_for_display,
    project_root_from_control_files,
    project_root_from_lsof_sensors_socket,
    read_process_cwd,
    try_parse_sensors_start,
)

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="Sensors listing uses Unix process tables")


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [
        (
            ["python3", "-m", "sensors.cli", "start", "--worker", "/tmp/proj"],
            ("worker", "/tmp/proj"),
        ),
        (
            ["python3", "-m", "sensors.cli", "start", "--show", "/tmp/p2"],
            ("show", "/tmp/p2"),
        ),
        (
            ["python3", "-m", "sensors.cli", "start", "-c", "cfg.yaml", "--worker", "/abs/here"],
            ("worker", "/abs/here"),
        ),
        (
            ["uv", "run", "sensors", "start", "--worker", "/uv/proj"],
            ("worker", "/uv/proj"),
        ),
        (
            ["/opt/bin/sensors", "start", "--show", "/cli"],
            ("show", "/cli"),
        ),
        (
            [r"C:\Users\gavin\.local\bin\sensors.exe", "start", "--worker", r"G:\proj\java"],
            ("worker", r"G:\proj\java"),
        ),
        (
            [r"G:\proj\dist\sensors.EXE", "start", "--show", r"C:\cli"],
            ("show", r"C:\cli"),
        ),
    ],
)
def test_try_parse_sensors_start_matches(
    tokens: list[str],
    expected: tuple[str, str],
) -> None:
    got = try_parse_sensors_start(tokens)
    assert got is not None
    mode, path = got
    assert mode == expected[0]
    assert path == expected[1]


@pytest.mark.parametrize(
    "tokens",
    [
        ["python3", "-m", "pip", "install", "x"],
        ["bash", "-c", "echo"],
        ["python3", "-m", "sensors.cli", "check", "."],
        [r"C:\opt\mysensors.exe", "start", "--worker", r"C:\proj"],
    ],
)
def test_try_parse_sensors_start_rejects(tokens: list[str]) -> None:
    assert try_parse_sensors_start(tokens) is None


def test_read_process_cwd_matches_own_process() -> None:
    cwd = read_process_cwd(os.getpid())
    assert cwd is not None
    assert os.path.samefile(cwd, os.getcwd())


def test_format_folder_for_display_shortens_home() -> None:
    home = str(Path.home())
    sub = str(Path(home) / "projects" / "demo")
    got = format_folder_for_display(sub)
    assert got.startswith("~/")
    assert "projects/demo" in got.replace("\\", "/")


def test_looks_like_macos_temp_sensors_dir() -> None:
    assert looks_like_macos_temp_sensors_dir("/private/var/folders/xx/yy/T/tmpabc/proj")
    assert looks_like_macos_temp_sensors_dir("/var/folders/xx/yy/T/tmpabc/proj")
    assert not looks_like_macos_temp_sensors_dir("/Users/x/projects/repo")


def test_project_root_from_control_files_matches_pid(tmp_path: Path) -> None:
    sensors = tmp_path / ".sensors"
    sensors.mkdir()
    sock = sensors / "e2e.sock"
    sock.write_bytes(b"")
    pid = 424242
    ctl = sensors / "e2e.control.json"
    ctl.write_text(
        json.dumps(
            {
                "socketPath": str(sock.resolve()),
                "pid": pid,
                "config": "e2e.sensors.yaml",
            }
        ),
        encoding="utf-8",
    )
    got = project_root_from_control_files(pid, [tmp_path])
    assert got is not None
    assert got.resolve() == tmp_path.resolve()


def test_normalize_path_for_display_strips_private_prefix_on_macos() -> None:
    if sys.platform == "darwin":
        assert normalize_path_for_display("/private/var/folders/z").startswith("/var/")
        assert not normalize_path_for_display("/private/var/folders/z").startswith("/private/")
    else:
        assert normalize_path_for_display("/private/var/x") == "/private/var/x"


def test_project_root_from_lsof_sensors_socket_parses_n_lines() -> None:
    fake = "p12345\nfcwd\nn/tmp/sensors-proj/.sensors/e2e.sock\n"
    with patch(
        "sensors.orchestration.sensors_processes.subprocess.check_output",
        return_value=fake,
    ):
        got = project_root_from_lsof_sensors_socket(12345)
    assert got is not None
    assert got.resolve() == Path("/tmp/sensors-proj").resolve()


def test_project_root_from_control_files_wrong_pid(tmp_path: Path) -> None:
    sensors = tmp_path / ".sensors"
    sensors.mkdir()
    sock = sensors / "e2e.sock"
    sock.write_bytes(b"")
    ctl = sensors / "e2e.control.json"
    ctl.write_text(
        json.dumps({"socketPath": str(sock.resolve()), "pid": 1, "config": "e2e.sensors.yaml"}),
        encoding="utf-8",
    )
    assert project_root_from_control_files(999999, [tmp_path]) is None


def test_project_root_from_control_files_prefers_working_dir(tmp_path: Path) -> None:
    """When control.json has a workingDir field, use it instead of deriving from socketPath."""
    sensors = tmp_path / ".sensors"
    sensors.mkdir()
    sock = sensors / "e2e.sock"
    sock.write_bytes(b"")
    pid = 424242
    original_wd = str(tmp_path / "original-project")
    ctl = sensors / "e2e.control.json"
    ctl.write_text(
        json.dumps(
            {
                "socketPath": str(sock.resolve()),
                "pid": pid,
                "config": "e2e.sensors.yaml",
                "workingDir": original_wd,
            }
        ),
        encoding="utf-8",
    )
    got = project_root_from_control_files(pid, [tmp_path])
    assert got is not None
    # Should use workingDir, not socketPath parent
    assert str(got).endswith("original-project")


@pytest.mark.parametrize(
    ("path", "expected_contains"),
    [
        ("/private/var/folders/t5/abc/T/tmpXYZ/proj", "tmpXYZ/proj"),
        ("/var/folders/t5/abc/T/tmpXYZ/proj", "tmpXYZ/proj"),
    ],
)
def test_compact_temp_path(path: str, expected_contains: str) -> None:
    result = compact_temp_path(path)
    assert result is not None
    assert expected_contains in result
    assert result.startswith("(temp) ")


def test_compact_temp_path_returns_none_for_normal_path() -> None:
    assert compact_temp_path("/Users/x/projects/repo") is None
