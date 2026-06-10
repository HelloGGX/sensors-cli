"""Unit tests for sensors check CLI helpers (phase 5 refactor safety net)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sensors.cli import (
    _check_exit_code,
    _format_runner_dir,
    _print_runner_result,
    _relativize_runner_configs,
    _runner_status_text,
)
from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.persistence.models import FormattedOutput, RunnerState, SensorsState


def _runner_state(
    status: str,
    *,
    details_llm: str = "ok",
    failures_llm: str = "",
) -> RunnerState:
    return RunnerState(
        lastRun=datetime(2025, 1, 1, 12, 0, 0),
        status=status,  # type: ignore[arg-type]
        formatted=FormattedOutput(
            details_llm=details_llm,
            failures_llm=failures_llm,
        ),
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("success", "SUCCESS"),
        ("below_threshold", "SUCCESS (below threshold)"),
        ("failure", "FAILURE"),
    ],
)
def test_runner_status_text(status: str, expected: str) -> None:
    assert _runner_status_text(_runner_state(status)) == expected


def test_check_exit_code_all_success() -> None:
    state = SensorsState(
        lastUpdated=datetime(2025, 1, 1),
        runners={"a": _runner_state("success")},
    )
    assert _check_exit_code(state) == 0


def test_check_exit_code_below_threshold() -> None:
    state = SensorsState(
        lastUpdated=datetime(2025, 1, 1),
        runners={"a": _runner_state("below_threshold")},
    )
    assert _check_exit_code(state) == 3


def test_check_exit_code_failure() -> None:
    state = SensorsState(
        lastUpdated=datetime(2025, 1, 1),
        runners={"a": _runner_state("failure")},
    )
    assert _check_exit_code(state) == 1


def test_check_exit_code_failure_overrides_below_threshold() -> None:
    state = SensorsState(
        lastUpdated=datetime(2025, 1, 1),
        runners={
            "ok": _runner_state("below_threshold"),
            "bad": _runner_state("failure"),
        },
    )
    assert _check_exit_code(state) == 1


def test_print_runner_result_includes_status_and_failures(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = RunnerConfig(
        name="lint",
        parser="ruff",
        mode=RunnerMode.INTERVAL,
        command="ruff check .",
        interval=30_000,
        prompt="fix lint issues",
    )
    rs = _runner_state("failure", details_llm="3 issues", failures_llm="E001 bad\nE002 worse")
    now = datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
    state = SensorsState(lastUpdated=datetime(2025, 1, 1), runners={"lint": rs})

    _print_runner_result("lint", rs, {"lint": cfg}, state, now)
    out = capsys.readouterr().out

    assert "lint: FAILURE (3 issues)" in out
    assert "cmd: `ruff check .`" in out
    assert "prompt: fix lint issues" in out
    assert "E001 bad" in out
    assert "E002 worse" in out


# --- _format_runner_dir ---

def test_format_runner_dir_omits_when_matches_root() -> None:
    assert _format_runner_dir("/proj", "/proj") is None


def test_format_runner_dir_returns_relative_when_subdir() -> None:
    assert _format_runner_dir("/proj/frontend", "/proj") == "frontend"


def test_format_runner_dir_returns_absolute_when_outside_root() -> None:
    assert _format_runner_dir("/other/path", "/proj") == "/other/path"


def test_format_runner_dir_returns_dir_when_no_root() -> None:
    assert _format_runner_dir("/proj/frontend", None) == "/proj/frontend"


# --- _relativize_runner_configs ---

def _make_config(name: str, working_dir: str | None = None) -> RunnerConfig:
    return RunnerConfig(
        name=name,
        parser="default",
        mode=RunnerMode.INTERVAL,
        command="run",
        interval=30_000,
        workingDir=working_dir,
    )


def test_relativize_runner_configs_strips_root_dir() -> None:
    configs = {"a": _make_config("a", "/proj")}
    result = _relativize_runner_configs(configs, "/proj")
    assert result["a"].workingDir is None


def test_relativize_runner_configs_makes_subdir_relative() -> None:
    configs = {"a": _make_config("a", "/proj/frontend")}
    result = _relativize_runner_configs(configs, "/proj")
    assert result["a"].workingDir == "frontend"


def test_relativize_runner_configs_does_not_mutate_original() -> None:
    cfg = _make_config("a", "/proj/frontend")
    configs = {"a": cfg}
    _relativize_runner_configs(configs, "/proj")
    assert cfg.workingDir == "/proj/frontend"


# --- _print_runner_result dir display ---

def test_print_runner_result_omits_dir_when_same_as_root(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _make_config("lint", working_dir=None)  # already relativized: None means same as root
    rs = _runner_state("success")
    now = datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
    state = SensorsState(lastUpdated=datetime(2025, 1, 1), runners={"lint": rs})

    _print_runner_result("lint", rs, {"lint": cfg}, state, now)
    out = capsys.readouterr().out

    assert "dir:" not in out


def test_print_runner_result_shows_relative_dir_for_subdir(capsys: pytest.CaptureFixture[str]) -> None:
    cfg = _make_config("lint", working_dir="frontend")  # already relativized
    rs = _runner_state("success")
    now = datetime(2025, 1, 1, 12, 5, 0, tzinfo=timezone.utc)
    state = SensorsState(lastUpdated=datetime(2025, 1, 1), runners={"lint": rs})

    _print_runner_result("lint", rs, {"lint": cfg}, state, now)
    out = capsys.readouterr().out

    assert "dir: frontend" in out
