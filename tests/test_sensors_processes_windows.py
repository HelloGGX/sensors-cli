"""Unit tests for the Windows process enumeration (PowerShell CIM JSON path)."""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from sensors.orchestration.sensors_processes import (
    _windows_cmdline_tokens,
    _windows_rows_from_json,
    _windows_sensors_rows,
    list_sensors_start_pids,
    try_parse_sensors_start,
)

_WORKER_CMD = r"C:\py\python.exe -m sensors.cli start --worker C:\proj\java"
_WORKER_TOKENS = [
    r"C:\py\python.exe",
    "-m",
    "sensors.cli",
    "start",
    "--worker",
    r"C:\proj\java",
]


def test_windows_cmdline_tokens_keeps_backslash_paths() -> None:
    tokens = _windows_cmdline_tokens(_WORKER_CMD)
    assert tokens == _WORKER_TOKENS
    assert try_parse_sensors_start(tokens) == ("worker", r"C:\proj\java")


def test_windows_cmdline_tokens_strips_surrounding_quotes() -> None:
    tokens = _windows_cmdline_tokens(
        '"C:\\Program Files\\Python313\\python.exe" -m sensors.cli '
        'start --worker "C:\\my proj"'
    )
    assert tokens[0] == "C:\\Program Files\\Python313\\python.exe"
    assert tokens[-1] == "C:\\my proj"
    assert try_parse_sensors_start(tokens) == ("worker", "C:\\my proj")


def test_windows_rows_from_json_parses_array() -> None:
    payload = json.dumps(
        [
            {"ProcessId": 11, "ParentProcessId": 9, "CommandLine": _WORKER_CMD},
            {"ProcessId": 12, "ParentProcessId": 11, "CommandLine": None},
            {"ProcessId": 13, "ParentProcessId": 9},
        ]
    )
    rows = _windows_rows_from_json(payload)
    assert rows == [(11, 9, _WORKER_TOKENS)]


def test_windows_rows_from_json_parses_single_object() -> None:
    payload = json.dumps({"ProcessId": 13, "ParentProcessId": 9, "CommandLine": _WORKER_CMD})
    assert _windows_rows_from_json(payload) == [(13, 9, _WORKER_TOKENS)]


@pytest.mark.parametrize("bad", ["", "   ", "not json"])
def test_windows_rows_from_json_tolerates_bad_output(bad: str) -> None:
    assert _windows_rows_from_json(bad) == []


def test_windows_sensors_rows_collapses_uv_trampoline() -> None:
    trampoline = [
        r"G:\proj\.venv\Scripts\python.exe",
        "-m",
        "sensors.cli",
        "start",
        "--worker",
        r"C:\proj",
    ]
    real = [r"C:\base\python.exe", "-m", "sensors.cli", "start", "--worker", r"C:\proj"]
    unrelated = [r"C:\Windows\explorer.exe"]
    fake_rows = [
        (100, 9, trampoline),
        (101, 100, real),
        (102, 9, unrelated),
    ]
    with patch(
        "sensors.orchestration.sensors_processes._iter_windows_processes",
        return_value=fake_rows,
    ):
        rows = _windows_sensors_rows()
    assert rows == [(101, real)]


def test_windows_sensors_rows_keeps_plain_worker() -> None:
    real = [r"C:\py\python.exe", "-m", "sensors.cli", "start", "--show", r"C:\proj"]
    with patch(
        "sensors.orchestration.sensors_processes._iter_windows_processes",
        return_value=[(200, 9, real)],
    ):
        rows = _windows_sensors_rows()
    assert rows == [(200, real)]


def test_windows_sensors_rows_matches_frozen_exe_worker() -> None:
    """PyInstaller workers run as ``sensors.exe start --worker ...`` (regression).

    Frozen-exe workers used to be invisible to ``status --all``: the argv
    matcher only accepted an executable named exactly ``sensors``.
    """
    exe_worker = [
        r"C:\Users\gavin\.local\bin\sensors.exe",
        "start",
        "--worker",
        r"C:\proj\java",
    ]
    with patch(
        "sensors.orchestration.sensors_processes._iter_windows_processes",
        return_value=[(300, 9, exe_worker)],
    ):
        rows = _windows_sensors_rows()
    assert rows == [(300, exe_worker)]


def test_try_parse_sensors_start_matches_frozen_exe() -> None:
    """Runs on Windows hosts, where the sibling Unix-table suite is skipped."""
    tokens = [r"C:\Users\gavin\.local\bin\sensors.exe", "start", "--worker", r"C:\proj\java"]
    assert try_parse_sensors_start(tokens) == ("worker", r"C:\proj\java")
    lookalike = [r"C:\opt\mysensors.exe", "start", "--worker", r"C:\proj"]
    assert try_parse_sensors_start(lookalike) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only code path")
def test_list_sensors_start_pids_windows_uses_deduped_rows() -> None:
    trampoline = [
        r"G:\proj\.venv\Scripts\python.exe",
        "-m",
        "sensors.cli",
        "start",
        "--worker",
        r"C:\proj",
    ]
    real = [r"C:\base\python.exe", "-m", "sensors.cli", "start", "--worker", r"C:\proj"]
    with patch(
        "sensors.orchestration.sensors_processes._iter_windows_processes",
        return_value=[(100, 9, trampoline), (101, 100, real)],
    ):
        assert list_sensors_start_pids() == {101}
