"""Schema tests for runner fields (e.g. optional ``result``)."""

import pytest

from sensors.config.schema import RunnerConfig, RunnerMode


def test_runner_config_result_optional():
    r = RunnerConfig(
        name="x",
        parser="eslint",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="eslint .",
        interval=1000,
    )
    assert r.result is None


def test_runner_config_command_timeout_optional():
    r = RunnerConfig(
        name="x",
        parser="pytest",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="pytest",
        interval=1000,
    )
    assert r.commandTimeout is None


def test_runner_config_command_timeout_zero_means_unlimited():
    r = RunnerConfig(
        name="x",
        parser="stryker",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="npm run test:mutation",
        interval=60_000,
        commandTimeout=0,
    )
    assert r.commandTimeout == 0


def test_runner_config_result_set():
    r = RunnerConfig(
        name="x",
        parser="stryker",
        enabled=False,
        mode=RunnerMode.INTERVAL,
        command="npm run test:mutation",
        interval=60_000,
        result="reports/mutation/mutation.json",
    )
    assert r.result == "reports/mutation/mutation.json"


def test_runner_config_triggered_no_interval():
    r = RunnerConfig(
        name="heavy",
        parser="vitest",
        enabled=True,
        mode=RunnerMode.TRIGGERED,
        command="npm test",
    )
    assert r.interval is None
    assert r.mode == "triggered"


def test_runner_config_triggered_with_interval_rejected():
    with pytest.raises(ValueError, match="triggered"):
        RunnerConfig(
            name="x",
            parser="vitest",
            enabled=True,
            mode=RunnerMode.TRIGGERED,
            command="npm test",
            interval=5000,
        )


def test_runner_config_on_check_mode_no_parser():
    r = RunnerConfig(
        name="versions",
        enabled=True,
        mode=RunnerMode.ON_CHECK,
        command="node --version",
    )
    assert r.mode == "on_check"
    assert r.parser is None
    assert r.interval is None


def test_runner_config_non_on_check_requires_parser():
    with pytest.raises(ValueError, match="parser"):
        RunnerConfig(
            name="x",
            enabled=True,
            mode=RunnerMode.INTERVAL,
            command="do-thing",
            interval=1000,
        )


def test_runner_config_on_check_with_interval_rejected():
    with pytest.raises(ValueError, match="on_check"):
        RunnerConfig(
            name="x",
            enabled=True,
            mode=RunnerMode.ON_CHECK,
            command="echo hi",
            interval=1000,
        )


def test_runner_config_threshold_optional():
    r = RunnerConfig(
        name="coverage",
        parser="pytest_cov",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="pytest --cov",
        interval=30_000,
    )
    assert r.threshold is None


def test_runner_config_threshold_set():
    r = RunnerConfig(
        name="coverage",
        parser="pytest_cov",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="pytest --cov",
        interval=30_000,
        threshold=80.0,
    )
    assert r.threshold == 80.0
