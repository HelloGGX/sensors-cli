"""Tests for runner threshold feature (below_threshold status)."""

import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.config import FormattedOutput, ParsedOutput, RunnerResult, ScoreInfo
from sensors.persistence.models import RunnerState
from sensors.persistence.state_manager import StateManager
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.base import OutputParser
from sensors.tui.display import DisplayManager


class _FakeParser(OutputParser):
    """Minimal parser that returns a fixed ParsedOutput."""

    def __init__(self, score_value: int, direction: str = "more"):
        self._score_value = score_value
        self._direction = direction

    def parse(self, output: str) -> ParsedOutput:
        return ParsedOutput(
            success=True,
            summary=f"coverage: {self._score_value}%",
            score=ScoreInfo(
                value=self._score_value,
                direction=self._direction,
                description="coverage %",
            ),
        )


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

        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

        state = await sm.read_state()
        rs = state.runners["coverage"]
        assert rs.status == "below_threshold"
        assert "below target threshold of 80" in rs.formatted.details_llm
        assert "[yellow]" in rs.formatted.details_terminal
        assert "below target threshold of 80" in rs.formatted.details_terminal


@pytest.mark.asyncio
async def test_on_result_meets_threshold_more_direction():
    """Score at or above threshold → remains success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=80.0)
        runner = GenericRunner(cfg, _FakeParser(score_value=80), state_manager=sm)

        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

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

        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

        state = await sm.read_state()
        rs = state.runners["violations"]
        assert rs.status == "below_threshold"
        assert "below target threshold of 5" in rs.formatted.details_llm


@pytest.mark.asyncio
async def test_score_threshold_fallback_no_config_threshold():
    """score.threshold triggers below_threshold when config.threshold is not set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=None)

        class _CovParser(OutputParser):
            def parse(self, output: str) -> ParsedOutput:
                return ParsedOutput(
                    success=True,
                    summary="70% coverage",
                    score=ScoreInfo(value=70, direction="more", description="cov", threshold=80),
                )

        runner = GenericRunner(cfg, _CovParser(), state_manager=sm)
        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

        state = await sm.read_state()
        rs = state.runners["coverage"]
        assert rs.status == "below_threshold"
        assert "[yellow]" in rs.formatted.details_terminal
        assert "below target threshold of 80" in rs.formatted.details_llm


@pytest.mark.asyncio
async def test_on_result_no_threshold_stays_success():
    """Without threshold, a passing runner stays success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=None)
        runner = GenericRunner(cfg, _FakeParser(score_value=50), state_manager=sm)

        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

        state = await sm.read_state()
        assert state.runners["coverage"].status == "success"


@pytest.mark.asyncio
async def test_on_result_failure_not_converted_to_below_threshold():
    """A hard failure is never converted to below_threshold even if score is set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfg = _make_config(threshold=80.0)
        runner = GenericRunner(cfg, _FakeParser(score_value=0), state_manager=sm)

        # Override the parser to return failure so on_result sees success=False.
        class _FailParser(_FakeParser):
            def parse(self, output: str) -> ParsedOutput:
                p = super().parse(output)
                p.success = False
                return p

        runner.parser = _FailParser(score_value=0)
        parsed, result = await runner._parse_input("")
        await runner.on_result(result, parsed)

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
