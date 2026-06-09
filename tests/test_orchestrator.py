"""Unit tests for orchestrator helpers and event dispatch."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sensors.config.schema import RunnerConfig, RunnerMode, SensorsConfig
from sensors.orchestration.orchestrator import (
    OrchestratorEventType,
    _build_runners,
    _cancel_batch_waits,
    _log_unexpected_runner_exits,
    _wait_for_orchestrator_event,
    _wire_runner_batch,
)
from sensors.persistence.state_manager import StateManager
from sensors.events import DisplayEvents


def _minimal_config(*runners: RunnerConfig) -> SensorsConfig:
    return SensorsConfig(version=1, runners=list(runners))


@pytest.mark.asyncio
async def test_build_runners_skips_on_check_and_disabled() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        config = _minimal_config(
            RunnerConfig(
                name="check-only",
                mode=RunnerMode.ON_CHECK,
                command="true",
            ),
            RunnerConfig(
                name="disabled",
                parser="pytest",
                mode=RunnerMode.INTERVAL,
                interval=60,
                command="true",
                enabled=False,
            ),
            RunnerConfig(
                name="interval",
                parser="pytest",
                mode=RunnerMode.INTERVAL,
                interval=60,
                command="true",
            ),
        )
        runners, rerun_events = _build_runners(config, sm)
        assert [r.config.name for r in runners] == ["interval"]
        assert set(rerun_events) == {"interval"}


@pytest.mark.asyncio
async def test_wait_for_orchestrator_event_snapshot() -> None:
    events = DisplayEvents()
    batch = _wire_runner_batch([], events)
    events.snapshot.set()
    event, _done = await _wait_for_orchestrator_event(batch)
    assert event == OrchestratorEventType.SNAPSHOT
    _cancel_batch_waits(batch)


@pytest.mark.asyncio
async def test_wait_for_orchestrator_event_shutdown() -> None:
    events = DisplayEvents()
    batch = _wire_runner_batch([], events)
    events.shutdown.set()
    event, _done = await _wait_for_orchestrator_event(batch)
    assert event == OrchestratorEventType.SHUTDOWN
    _cancel_batch_waits(batch)


@pytest.mark.asyncio
async def test_wait_for_orchestrator_event_clear() -> None:
    events = DisplayEvents()
    batch = _wire_runner_batch([], events)
    events.clear.set()
    event, _done = await _wait_for_orchestrator_event(batch)
    assert event == OrchestratorEventType.CLEAR
    _cancel_batch_waits(batch)


@pytest.mark.asyncio
async def test_wait_for_orchestrator_event_runner_exit() -> None:
    async def finish_immediately() -> None:
        return

    task = asyncio.create_task(finish_immediately())
    events = DisplayEvents()
    batch = _wire_runner_batch([task], events)
    event, done = await _wait_for_orchestrator_event(batch)
    assert event == OrchestratorEventType.RUNNER_EXIT
    assert task in done
    _cancel_batch_waits(batch)


@pytest.mark.asyncio
async def test_log_unexpected_runner_exits_with_exception(caplog) -> None:
    async def boom() -> None:
        raise RuntimeError("runner blew up")

    task = asyncio.create_task(boom())
    with pytest.raises(RuntimeError):
        await task
    runner = MagicMock()
    runner.config.name = "smoke"
    with caplog.at_level("ERROR"):
        _log_unexpected_runner_exits({task}, [task], [runner])
    assert "smoke" in caplog.text
    assert "exception" in caplog.text.lower()


@pytest.mark.asyncio
async def test_log_unexpected_runner_exits_without_exception(caplog) -> None:
    async def finish() -> None:
        return

    task = asyncio.create_task(finish())
    await task
    runner = MagicMock()
    runner.config.name = "smoke"
    with caplog.at_level("ERROR"):
        _log_unexpected_runner_exits({task}, [task], [runner])
    assert "smoke" in caplog.text
    assert "no exception" in caplog.text.lower()


@pytest.mark.asyncio
async def test_on_snapshot_saves_state_and_clears_event() -> None:
    from sensors.orchestration.orchestrator import _on_snapshot

    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        events = DisplayEvents()
        events.snapshot.set()
        batch = _wire_runner_batch([], events)
        ctx = MagicMock(state_manager=sm, events=events, display=None)
        await _on_snapshot(ctx, batch)
        assert not events.snapshot.is_set()
        state_file = Path(tmpdir) / "state.json"
        assert state_file.exists()
        assert b"snapshot" in state_file.read_bytes()
