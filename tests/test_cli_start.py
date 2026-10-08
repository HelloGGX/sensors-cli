"""Unit tests for sensors start helpers (no-runner fast path, control-file wait)."""

from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sensors import cli
from sensors.cli import _has_daemon_runners, _spawn_background_worker, _wait_for_control_file
from sensors.config.schema import RunnerConfig, RunnerMode, SensorsConfig


class _AliveProc:
    def poll(self) -> int | None:
        return None

    @property
    def returncode(self) -> int | None:
        return None


class _ExitedProc:
    def poll(self) -> int | None:
        return 2

    @property
    def returncode(self) -> int | None:
        return 2


def _runner(mode: RunnerMode = RunnerMode.INTERVAL, *, enabled: bool = True) -> RunnerConfig:
    return RunnerConfig(
        name="t",
        parser="default",
        enabled=enabled,
        mode=mode,
        command="run",
        **({} if mode == RunnerMode.ON_CHECK else {"interval": 30_000}),
    )


def _cfg(*runners: RunnerConfig) -> SensorsConfig:
    return SensorsConfig(version=1, runners=list(runners))


def test_has_daemon_runners_true_for_enabled_interval_runner() -> None:
    assert _has_daemon_runners(_cfg(_runner())) is True


def test_has_daemon_runners_false_when_all_disabled() -> None:
    assert _has_daemon_runners(_cfg(_runner(enabled=False))) is False


def test_has_daemon_runners_false_when_only_on_check_enabled() -> None:
    assert _has_daemon_runners(_cfg(_runner(mode=RunnerMode.ON_CHECK))) is False


def test_has_daemon_runners_false_for_empty_config() -> None:
    assert _has_daemon_runners(_cfg()) is False


def test_wait_for_control_file_returns_true_when_file_exists(tmp_path: Path) -> None:
    ctl = tmp_path / "x.control.json"
    ctl.write_text("{}", encoding="utf-8")
    assert _wait_for_control_file(ctl, _AliveProc(), timeout_sec=0.2) is True


def test_wait_for_control_file_fails_fast_when_child_exits(tmp_path: Path) -> None:
    start = time.monotonic()
    got = _wait_for_control_file(tmp_path / "missing.json", _ExitedProc(), timeout_sec=5.0)
    assert got is False
    assert time.monotonic() - start < 1.0


def test_wait_for_control_file_times_out_when_child_alive(tmp_path: Path) -> None:
    assert _wait_for_control_file(tmp_path / "missing.json", _AliveProc(), timeout_sec=0.2) is False


@pytest.mark.parametrize("os_name", ["posix", "nt"])
@pytest.mark.parametrize("frozen", [False, True])
def test_background_worker_owns_frozen_bundle_without_changing_parent_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, os_name: str, frozen: bool
) -> None:
    environment = {"PATH": "existing-path", "PYINSTALLER_RESET_ENVIRONMENT": "0"}
    monkeypatch.setattr(cli, "os", SimpleNamespace(name=os_name, environ=environment))
    monkeypatch.setattr(cli.sys, "frozen", frozen, raising=False)
    popen = Mock()
    monkeypatch.setattr(cli.subprocess, "Popen", popen)

    process = _spawn_background_worker(
        str(tmp_path), "smoke.sensors.yaml", tmp_path / ".sensors" / "worker.log"
    )

    assert process is popen.return_value
    popen.assert_called_once()
    child_environment = popen.call_args.kwargs.get("env")
    if frozen:
        # A daemon must unpack its own bundle: the launching CLI exits and
        # PyInstaller then removes that CLI's temporary extracted resources.
        assert child_environment == {"PATH": "existing-path", "PYINSTALLER_RESET_ENVIRONMENT": "1"}
        assert child_environment is not environment
        assert popen.call_args.args[0] == [
            cli.sys.executable,
            "start",
            "--worker",
            str(tmp_path),
            "--config",
            "smoke.sensors.yaml",
        ]
    else:
        assert child_environment is None
        assert popen.call_args.args[0][:3] == [cli.sys.executable, "-m", "sensors.cli"]
    assert environment == {"PATH": "existing-path", "PYINSTALLER_RESET_ENVIRONMENT": "0"}
