"""Tests for runner ``result`` file path (parse report from disk after command)."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from sensors.cli import _runner_description
from sensors.config.loader import ConfigLoadError, load_config
from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.persistence.models import RunnerResult
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.eslint import ESLintParser


@pytest.mark.asyncio
async def test_load_config_resolves_result_relative_to_project(tmp_path: Path):
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (tmp_path / "subdir").mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: stryker\n"
        "    parser: stryker\n"
        "    enabled: false\n"
        "    mode: interval\n"
        "    command: npm run test:mutation\n"
        "    interval: 60000\n"
        "    result: subdir/out.json\n"
    )

    config = await load_config(str(tmp_path))
    assert config.runners[0].result == str((tmp_path / "subdir" / "out.json").resolve())


@pytest.mark.asyncio
async def test_load_config_rejects_result_outside_project(tmp_path: Path):
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: x\n"
        "    parser: eslint\n"
        "    enabled: false\n"
        "    mode: interval\n"
        "    command: echo\n"
        "    interval: 1000\n"
        "    result: ../../../etc/passwd\n"
    )

    with pytest.raises(ConfigLoadError, match="result must stay within"):
        await load_config(str(tmp_path))


@pytest.mark.asyncio
async def test_parser_input_from_run_uses_file_when_result_set(tmp_path: Path):
    report = tmp_path / "report.json"
    report.write_text("[]", encoding="utf-8")

    cfg = RunnerConfig(
        name="t",
        parser="eslint",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="echo hi",
        interval=1000,
        result=str(report),
    )
    runner = GenericRunner(cfg, ESLintParser(), state_manager=None)
    out = await runner._parser_input_from_run("THIS_STDOUT_SHOULD_NOT_BE_USED")
    assert out == "[]"


@pytest.mark.asyncio
async def test_parser_input_from_run_missing_file_returns_runner_result(tmp_path: Path):
    missing = tmp_path / "nope.json"
    cfg = RunnerConfig(
        name="t",
        parser="eslint",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="echo hi",
        interval=1000,
        result=str(missing),
    )
    runner = GenericRunner(cfg, ESLintParser(), state_manager=None)
    res = await runner._parser_input_from_run("stdout context")

    assert isinstance(res, RunnerResult)
    assert not res.success
    assert "Could not read result file" in res.output.get("parseError", "")
    assert "stdout context" in res.output.get("raw", "")


@pytest.mark.asyncio
async def test_load_config_multiple_runners_result_resolved(tmp_path: Path):
    """``result`` is resolved for the runner that has it; coexists with other runners."""
    sensors_dir = tmp_path / ".sensors"
    sensors_dir.mkdir()
    (tmp_path / "reports").mkdir()
    (sensors_dir / "test.sensors.yaml").write_text(
        "version: 1\n"
        "runners:\n"
        "  - name: stryker\n"
        "    parser: stryker\n"
        "    enabled: false\n"
        "    mode: interval\n"
        "    command: npm run test:mutation\n"
        "    interval: 60000\n"
        "    result: reports/m.json\n"
        "  - name: git-diff\n"
        "    parser: git_diff\n"
        "    enabled: true\n"
    )

    config = await load_config(str(tmp_path))
    assert len(config.runners) == 2
    assert config.runners[0].result == str((tmp_path / "reports" / "m.json").resolve())
    assert config.runners[1].result is None
    assert "git diff" in config.runners[1].command


def test_runner_description_includes_result_path():
    cfg = RunnerConfig(
        name="stryker",
        parser="stryker",
        enabled=False,
        mode=RunnerMode.INTERVAL,
        command="npm run test:mutation",
        interval=60_000,
        result="/proj/reports/mutation.json",
    )
    text = _runner_description(cfg)
    assert "cmd:" in text
    assert "result: /proj/reports/mutation.json" in text


@pytest.mark.asyncio
async def test_parser_input_from_run_retries_on_transient_file_not_found(tmp_path: Path):
    """File missing on first read (race with tool recreating it) but present on retry."""
    report = tmp_path / "coverage-final.json"
    calls = 0

    def flaky_read_text(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise FileNotFoundError("not yet written")
        return '{"ok": true}'

    cfg = RunnerConfig(
        name="t",
        parser="eslint",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="vitest run --coverage",
        interval=1000,
        result=str(report),
    )
    runner = GenericRunner(cfg, ESLintParser(), state_manager=None)

    with patch.object(Path, "read_text", flaky_read_text), patch(
        "asyncio.sleep", new_callable=AsyncMock
    ):
        out = await runner._parser_input_from_run("stdout")

    assert out == '{"ok": true}'
    assert calls == 3


@pytest.mark.asyncio
async def test_parser_input_without_result_passes_stdout():
    cfg = RunnerConfig(
        name="t",
        parser="eslint",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="echo hi",
        interval=1000,
    )
    runner = GenericRunner(cfg, ESLintParser(), state_manager=None)
    out = await runner._parser_input_from_run("hello")
    assert out == "hello"
