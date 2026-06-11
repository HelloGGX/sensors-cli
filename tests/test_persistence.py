"""Tests for persistence layer."""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from sensors.config import Formatted, ScoreInfo, SensorReading
from sensors.persistence import (
    HistoryEntry,
    RunnerEntry,
    RunnerSummary,
    StateEntry,
    StateManager,
)


def _reading(label: str = "ok", *, success: bool = True, score_value: int = 0) -> SensorReading:
    return SensorReading(
        success=success,
        summary=label,
        score=ScoreInfo(value=score_value, direction="less"),
    )


def _reading_with_formatted(formatted: Formatted, *, success: bool = True) -> SensorReading:
    """Build a SensorReading with a specific pre-set Formatted (summary_llm must be non-empty)."""
    return SensorReading(
        success=success,
        summary=formatted.summary_llm or "ok",
        score=ScoreInfo(value=0, direction="less"),
        formatted=formatted,
    )


@pytest.mark.asyncio
async def test_state_manager_read_empty_state():
    """Test reading state when file doesn't exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        state = await manager.read_state()

        assert isinstance(state, StateEntry)
        assert len(state.runners) == 0
        assert isinstance(state.lastUpdated, datetime)


@pytest.mark.asyncio
async def test_state_manager_update_and_read():
    """Test updating and reading state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=_reading_with_formatted(Formatted(
                summary_terminal="[red]2 errors, 1 warning[/red]",
                summary_html='<span class="sensors-error">2 errors, 1 warning</span>',
                summary_llm="2 errors, 1 warning",
                failures_llm="  src/main.ts:42:0 ERROR Missing semicolon",
            ), success=False),
        )

        await manager.update_state("eslint", runner_state)

        state = await manager.read_state()

        assert "eslint" in state.runners
        assert state.runners["eslint"].status == "failure"
        assert state.runners["eslint"].reading.formatted.summary_llm == "2 errors, 1 warning"
        assert state.runners["eslint"].reading.formatted.summary_terminal == "[red]2 errors, 1 warning[/red]"
        assert "sensors-error" in state.runners["eslint"].reading.formatted.summary_html


@pytest.mark.asyncio
async def test_state_manager_atomic_writes():
    """Test that writes are atomic (file always valid JSON)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        for i in range(5):
            runner_state = RunnerEntry(
                lastRun=datetime.utcnow(),
                status="success",
            )

            await manager.update_state(f"runner_{i}", runner_state)

            with open(state_file) as f:
                data = json.load(f)
                assert "lastUpdated" in data
                assert "runners" in data


@pytest.mark.asyncio
async def test_state_manager_multiple_runners():
    """Test updating multiple runners."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        eslint_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
        )
        await manager.update_state("eslint", eslint_state)

        test_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="success",
        )
        await manager.update_state("tests", test_state)

        state = await manager.read_state()

        assert len(state.runners) == 2
        assert "eslint" in state.runners
        assert "tests" in state.runners
        assert state.runners["eslint"].status == "failure"
        assert state.runners["tests"].status == "success"


@pytest.mark.asyncio
async def test_state_json_format():
    """Test that the JSON format matches the specification."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=_reading_with_formatted(Formatted(
                summary_terminal="[red]2 errors, 1 warning[/red]",
                summary_html='<span class="sensors-error">2 errors, 1 warning</span>',
                summary_llm="2 errors, 1 warning",
                failures_llm="  src/main.ts:42:0 ERROR Missing semicolon",
            ), success=False),
        )

        await manager.update_state("eslint", runner_state)

        with open(state_file) as f:
            data = json.load(f)

        assert "lastUpdated" in data
        assert "T" in data["lastUpdated"]
        assert "runners" in data
        assert "eslint" in data["runners"]

        eslint_data = data["runners"]["eslint"]
        assert "lastRun" in eslint_data
        assert "T" in eslint_data["lastRun"]
        assert "status" in eslint_data
        assert eslint_data["status"] == "failure"
        assert "result" not in eslint_data

        # Verify formatted output is nested under reading
        assert "reading" in eslint_data
        formatted = eslint_data["reading"]["formatted"]
        assert formatted["summary_llm"] == "2 errors, 1 warning"
        assert "sensors-error" in formatted["summary_html"]
        assert formatted["failures_llm"] == "  src/main.ts:42:0 ERROR Missing semicolon"


@pytest.mark.asyncio
async def test_reading_defaults_to_none():
    """Test that reading defaults to None when not provided."""
    runner_state = RunnerEntry(
        lastRun=datetime.utcnow(),
        status="success",
    )
    assert runner_state.reading is None


@pytest.mark.asyncio
async def test_score_persists_in_runner_state():
    """Test that score is persisted and read back correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=SensorReading(
                success=False,
                summary="3 errors",
                score=ScoreInfo(value=3, direction="less"),
            ),
        )

        await manager.update_state("eslint", runner_state)
        state = await manager.read_state()

        assert state.runners["eslint"].reading is not None
        assert state.runners["eslint"].reading.score.value == 3
        assert state.runners["eslint"].reading.score.direction == "less"


@pytest.mark.asyncio
async def test_score_in_json_format():
    """Test that score appears correctly in the JSON output."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="success",
            reading=_reading("all passed", score_value=0),
        )
        await manager.update_state("vitest", runner_state)

        with open(state_file) as f:
            data = json.load(f)

        assert "reading" in data["runners"]["vitest"]
        assert data["runners"]["vitest"]["reading"]["score"]["value"] == 0
        assert data["runners"]["vitest"]["reading"]["score"]["direction"] == "less"


@pytest.mark.asyncio
async def test_save_snapshot():
    """Test that save_snapshot copies current runners into snapshot field."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=SensorReading(
                success=False,
                summary="5 errors",
                score=ScoreInfo(value=5, direction="less"),
            ),
        )
        await manager.update_state("eslint", runner_state)

        await manager.save_snapshot()

        state = await manager.read_state()
        assert state.snapshot is not None
        assert isinstance(state.snapshot.snapshot_id, str)
        assert len(state.snapshot.snapshot_id) == 8
        assert "eslint" in state.snapshot.runners
        assert state.snapshot.runners["eslint"].reading.score.value == 5
        assert isinstance(state.snapshot.timestamp, datetime)


@pytest.mark.asyncio
async def test_snapshot_persists_through_updates():
    """Test that snapshot is preserved when runners update after snapshot."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state1 = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=SensorReading(
                success=False, summary="5 errors",
                score=ScoreInfo(value=5, direction="less"),
            ),
        )
        await manager.update_state("eslint", runner_state1)
        await manager.save_snapshot()

        runner_state2 = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="failure",
            reading=SensorReading(
                success=False, summary="3 errors",
                score=ScoreInfo(value=3, direction="less"),
            ),
        )
        await manager.update_state("eslint", runner_state2)

        state = await manager.read_state()
        assert state.snapshot.runners["eslint"].reading.score.value == 5
        assert state.runners["eslint"].reading.score.value == 3


@pytest.mark.asyncio
async def test_snapshot_in_json_format():
    """Test that snapshot appears correctly in JSON."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        manager = StateManager(state_file)

        runner_state = RunnerEntry(
            lastRun=datetime.utcnow(),
            status="success",
            reading=_reading("all passed"),
        )
        await manager.update_state("vitest", runner_state)
        await manager.save_snapshot()

        with open(state_file) as f:
            data = json.load(f)

        assert "snapshot" in data
        assert "snapshot_id" in data["snapshot"]
        assert len(data["snapshot"]["snapshot_id"]) == 8
        assert "T" in data["snapshot"]["timestamp"]
        assert "vitest" in data["snapshot"]["runners"]


@pytest.mark.asyncio
async def test_append_check_history_creates_file():
    """append_check_history writes a file that did not previously exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "foo.state.json"
        history_file = Path(tmpdir) / "foo.history.jsonl"
        manager = StateManager(state_file, history_file)

        entry = HistoryEntry(
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            runner_filter=None,
            runners={
                "pytest": RunnerSummary(status="failure", score=ScoreInfo(value=3, direction="less")),
                "eslint": RunnerSummary(status="success", score=None),
            },
        )

        await manager.append_check_history(entry)

        assert history_file.exists()
        lines = history_file.read_text().splitlines()
        assert len(lines) == 1

        record = json.loads(lines[0])
        assert record["timestamp"] == "2026-01-01T12:00:00Z"
        assert record["runner_filter"] is None
        assert "snapshot_id" not in record
        assert record["runners"]["pytest"]["status"] == "failure"
        assert record["runners"]["pytest"]["score"]["value"] == 3
        assert record["runners"]["eslint"]["status"] == "success"
        assert record["runners"]["eslint"]["score"] is None


@pytest.mark.asyncio
async def test_append_check_history_appends_multiple_entries():
    """Each call appends a new line; the file grows as a JSONL log."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "foo.state.json"
        history_file = Path(tmpdir) / "foo.history.jsonl"
        manager = StateManager(state_file, history_file)

        for i in range(3):
            entry = HistoryEntry(
                timestamp=datetime(2026, 1, 1, 12, i, 0),
                runner_filter=None,
                runners={"pytest": RunnerSummary(status="success")},
            )
            await manager.append_check_history(entry)

        lines = history_file.read_text().splitlines()
        assert len(lines) == 3
        for line in lines:
            json.loads(line)


@pytest.mark.asyncio
async def test_append_check_history_with_runner_filter():
    """runner_filter is stored when set."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "foo.state.json"
        history_file = Path(tmpdir) / "foo.history.jsonl"
        manager = StateManager(state_file, history_file)

        entry = HistoryEntry(
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            runner_filter="pytest",
            runners={"pytest": RunnerSummary(status="success")},
        )
        await manager.append_check_history(entry)

        record = json.loads(history_file.read_text().strip())
        assert record["runner_filter"] == "pytest"


@pytest.mark.asyncio
async def test_save_snapshot_writes_first_history_entry():
    """save_snapshot writes an initial history record tagged with the new snapshot_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "foo.state.json"
        history_file = Path(tmpdir) / "foo.history.jsonl"
        manager = StateManager(state_file, history_file)

        await manager.save_snapshot()

        state = await manager.read_state()
        assert state.snapshot is not None

        lines = history_file.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["snapshot_id"] == state.snapshot.snapshot_id
        assert record["runner_filter"] is None


@pytest.mark.asyncio
async def test_append_check_history_includes_snapshot_id_from_state():
    """When a snapshot exists in state, history lines include that snapshot_id."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "foo.state.json"
        history_file = Path(tmpdir) / "foo.history.jsonl"
        manager = StateManager(state_file, history_file)

        await manager.save_snapshot()

        state = await manager.read_state()
        assert state.snapshot is not None
        expected_snapshot_id = state.snapshot.snapshot_id

        entry = HistoryEntry(
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
            runner_filter=None,
            runners={"pytest": RunnerSummary(status="success")},
        )
        await manager.append_check_history(entry)

        lines = history_file.read_text().splitlines()
        assert len(lines) == 2
        record = json.loads(lines[1])
        assert record["snapshot_id"] == expected_snapshot_id


@pytest.mark.asyncio
async def test_state_manager_default_history_path():
    """StateManager defaults history_file to history.jsonl alongside the state file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "myproject.state.json"
        manager = StateManager(state_file)

        assert manager.history_file == Path(tmpdir) / "history.jsonl"
