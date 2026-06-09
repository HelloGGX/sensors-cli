"""Tests for runner threshold feature (below_threshold status)."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.config import FormattedOutput, RunnerResult, ScoreInfo
from sensors.persistence.models import RunnerState
from sensors.persistence.state_manager import StateManager
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.base import OutputParser
from sensors.tui.display import DisplayManager


class _FakeParser(OutputParser):
    """Minimal parser that returns a fixed result."""

    def __init__(self, score_value: int, direction: str = "more"):
        self._score_value = score_value
        self._direction = direction

    def parse_output(self, output: str) -> RunnerResult:
        return RunnerResult(
            timestamp=datetime.now(),
            success=True,
            output={"value": self._score_value},
        )

    def format_details_terminal(self, result: RunnerResult) -> str:
        return f"coverage: {result.output['value']}%"

    def format_details_html(self, result: RunnerResult) -> str:
        return f"coverage: {result.output['value']}%"

    def format_details_llm(self, result: RunnerResult) -> str:
        return f"coverage: {result.output['value']}%"

    def format_failures_terminal(self, result: RunnerResult) -> str:
        return ""

    def format_failures_html(self, result: RunnerResult) -> str:
        return ""

    def format_failures_llm(self, result: RunnerResult) -> str:
        return ""

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        return ScoreInfo(
            value=result.output["value"],
            direction=self._direction,
            description="coverage %",
        )

    def is_watch_run_complete(self, line: str) -> bool:
        return False


def _make_config(threshold: float | None, direction: str = "more") -> RunnerConfig:
    return RunnerConfig(
        name="coverage",
        parser="pytest_cov",
        enabled=True,
        mode=RunnerMode.INTERVAL,
        command="pytest --cov",
        interval=30_000,
        threshold=threshold,
    )


@pytest.mark.asyncio
async def test_on_result_below_threshold_more_direction():
    """Score below threshold with direction='more' → below_threshold status."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=80.0)
        runner = GenericRunner(cfg, _FakeParser(score_value=75), state_manager=sm)

        result = RunnerResult(
            timestamp=datetime.now(),
            success=True,
            output={"value": 75},
        )
        await runner.on_result(result)

        state = await sm.read_state()
        rs = state.runners["coverage"]
        assert rs.status == "below_threshold"
        assert "below target threshold of 80" in rs.formatted.details_llm
        assert "below target threshold of 80" in rs.formatted.details_terminal


@pytest.mark.asyncio
async def test_on_result_meets_threshold_more_direction():
    """Score at or above threshold → remains success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=80.0)
        runner = GenericRunner(cfg, _FakeParser(score_value=80), state_manager=sm)

        result = RunnerResult(
            timestamp=datetime.now(),
            success=True,
            output={"value": 80},
        )
        await runner.on_result(result)

        state = await sm.read_state()
        assert state.runners["coverage"].status == "success"


@pytest.mark.asyncio
async def test_on_result_below_threshold_less_direction():
    """Score above threshold with direction='less' → below_threshold."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = RunnerConfig(
            name="violations",
            parser="ruff",
            enabled=True,
            mode=RunnerMode.INTERVAL,
            command="ruff check .",
            interval=10_000,
            threshold=5.0,
        )
        runner = GenericRunner(cfg, _FakeParser(score_value=8, direction="less"), state_manager=sm)

        result = RunnerResult(
            timestamp=datetime.now(),
            success=True,
            output={"value": 8},
        )
        await runner.on_result(result)

        state = await sm.read_state()
        rs = state.runners["violations"]
        assert rs.status == "below_threshold"
        assert "below target threshold of 5" in rs.formatted.details_llm


@pytest.mark.asyncio
async def test_on_result_no_threshold_stays_success():
    """Without threshold, a passing runner stays success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=None)
        runner = GenericRunner(cfg, _FakeParser(score_value=50), state_manager=sm)

        result = RunnerResult(
            timestamp=datetime.now(),
            success=True,
            output={"value": 50},
        )
        await runner.on_result(result)

        state = await sm.read_state()
        assert state.runners["coverage"].status == "success"


@pytest.mark.asyncio
async def test_on_result_failure_not_converted_to_below_threshold():
    """A hard failure is never converted to below_threshold even if score is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=80.0)
        runner = GenericRunner(cfg, _FakeParser(score_value=0), state_manager=sm)

        result = RunnerResult(
            timestamp=datetime.now(),
            success=False,
            output={"value": 0},
        )
        await runner.on_result(result)

        state = await sm.read_state()
        assert state.runners["coverage"].status == "failure"


@pytest.mark.asyncio
async def test_display_below_threshold_shows_yellow_circle():
    """below_threshold status renders as 🟡 in the display table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")

        rs = RunnerState(
            lastRun=datetime.now(),
            status="below_threshold",
            formatted=FormattedOutput(
                details_terminal="coverage: 75% (below target threshold of 80)",
                details_llm="coverage: 75% (below target threshold of 80)",
            ),
            score=ScoreInfo(value=75, direction="more", description="coverage %"),
        )
        await sm.update_state("coverage", rs)

        runner_configs = [
            RunnerConfig(
                name="coverage",
                parser="pytest_cov",
                enabled=True,
                mode=RunnerMode.INTERVAL,
                command="pytest --cov",
                interval=30_000,
                threshold=80.0,
            )
        ]
        display = DisplayManager(sm, runner_configs=runner_configs)

        from rich.table import Table
        table = Table()
        table.add_column("St")
        await display._populate_table(table)

        # Check that the icon map has the right entry
        assert display._get_status_icon("below_threshold") == "🟡"
        assert display._get_status_icon("success") == "🟢"
        assert display._get_status_icon("failure") == "🔴"
