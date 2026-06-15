"""Tests for the display manager."""

import asyncio
import tempfile
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from rich.console import Console

from sensors.config import ScoreInfo, SensorReading
from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.events import DisplayEvents
from sensors.persistence.models import RunnerEntry
from sensors.persistence.state_manager import StateManager
from sensors.tui.display import DisplayManager


@pytest.fixture
async def state_manager_with_data():
    """Create a state manager with sample data."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        sm = StateManager(state_file)

        now_aware = datetime.now()
        runner_state = RunnerEntry(
            lastRun=now_aware,
            status="success",
            reading=SensorReading(
                success=True,
                summary="No issues",
                score=ScoreInfo(value=0, direction="less"),
            ),
        )

        await sm.update_state("eslint", runner_state)
        yield sm


@pytest.mark.asyncio
async def test_format_time_ago_with_naive_datetime(state_manager_with_data):
    """Test that _format_time_ago works with naive local datetimes."""
    display = DisplayManager(state_manager_with_data, update_interval=1.0)

    state = await state_manager_with_data.read_state()
    assert "eslint" in state.runners

    runner_state = state.runners["eslint"]
    timestamp = runner_state.lastRun

    result = display._format_time_ago(timestamp)

    assert result.endswith(" ago")
    assert any(unit in result for unit in ["s", "m", "h"])


@pytest.mark.asyncio
async def test_populate_table_with_state(state_manager_with_data):
    """Test that table population works with real state data."""
    display = DisplayManager(state_manager_with_data, update_interval=1.0)
    table = display._create_table()

    await display._populate_table(table)
    assert table is not None


@pytest.mark.asyncio
async def test_populate_table_enabled_but_not_active_shows_parser_skip_hint():
    """Enabled in YAML but no GenericRunner (unknown parser) — not 'waiting forever'."""
    sm = StateManager()
    cfgs = [
        RunnerConfig(
            name="stryker",
            parser="stryker",
            enabled=True,
            mode=RunnerMode.INTERVAL,
            command="npm run test:mutation",
            interval=120_000,
        ),
    ]
    display = DisplayManager(sm, runner_configs=cfgs, update_interval=1.0)
    display.set_runner_configs(cfgs, active_runner_names=set())
    table = display._create_table()
    await display._populate_table(table)
    buf = StringIO()
    Console(file=buf, force_terminal=True, width=220).print(table)
    rendered = buf.getvalue()
    assert "Not running" in rendered
    assert "reinstall" in rendered.lower()
    assert "Waiting to start" not in rendered


@pytest.mark.asyncio
async def test_populate_table_disabled_runner_not_waiting_to_start():
    """Disabled runners should not look like a hung first run."""
    sm = StateManager()
    cfgs = [
        RunnerConfig(
            name="stryker",
            parser="stryker",
            enabled=False,
            mode=RunnerMode.INTERVAL,
            command="npm run test:mutation",
            interval=120_000,
        ),
    ]
    display = DisplayManager(sm, runner_configs=cfgs, update_interval=1.0)
    table = display._create_table()
    await display._populate_table(table)
    buf = StringIO()
    Console(file=buf, force_terminal=True, width=120).print(table)
    rendered = buf.getvalue()
    assert "Disabled" in rendered
    assert "Waiting to start" not in rendered


@pytest.mark.asyncio
async def test_populate_table_reads_formatted_details():
    """Test that table population reads formatted.summary_terminal from state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file = Path(tmpdir) / "test_state.json"
        sm = StateManager(state_file)

        now = datetime.now()

        # ESLint with errors
        eslint_state = RunnerEntry(
            lastRun=now,
            status="failure",
            reading=SensorReading(
                success=False,
                summary="2 errors, 1 warning",
                score=ScoreInfo(value=2, direction="less"),
            ),
        )
        await sm.update_state("eslint", eslint_state)

        # Tests passing
        test_state = RunnerEntry(
            lastRun=now,
            status="success",
            reading=SensorReading(
                success=True,
                summary="10 passed",
                score=ScoreInfo(value=0, direction="less"),
            ),
        )
        await sm.update_state("tests", test_state)

        display = DisplayManager(sm, update_interval=1.0)
        table = display._create_table()
        await display._populate_table(table)
        assert table is not None


@pytest.mark.asyncio
async def test_format_time_ago_various_intervals():
    """Test time ago formatting for various time intervals."""
    display = DisplayManager(StateManager(), update_interval=1.0)

    timestamp = datetime.now() - timedelta(seconds=5)
    result = display._format_time_ago(timestamp)
    assert "s ago" in result


@pytest.mark.asyncio
async def test_get_status_icon():
    """Test status icon selection."""
    display = DisplayManager(StateManager(), update_interval=1.0)

    assert "🟢" in display._get_status_icon("success")
    assert "🔴" in display._get_status_icon("failure")
    assert "?" in display._get_status_icon("unknown")


def test_format_time_short_today():
    """SnapshotEntry taken today shows only HH:MM:SS, no date prefix."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    now = datetime.now()
    result = display._format_time_short(now)
    assert result == now.strftime("%H:%M:%S")
    assert "yesterday" not in result


def test_format_time_short_yesterday():
    """SnapshotEntry taken yesterday is prefixed with 'yesterday'."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    yesterday = datetime.now() - timedelta(days=1)
    result = display._format_time_short(yesterday)
    assert result.startswith("yesterday ")
    assert yesterday.strftime("%H:%M:%S") in result


def test_format_time_short_older():
    """SnapshotEntry older than yesterday shows abbreviated date."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    older = datetime.now() - timedelta(days=5)
    result = display._format_time_short(older)
    assert "yesterday" not in result
    assert older.strftime("%H:%M:%S") in result
    # Should contain an abbreviated month name
    assert older.strftime("%b") in result


def test_create_table_no_snapshot():
    """Table title contains no snapshot info when snapshot_time is None."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    table = display._create_table()
    assert "snapshot" not in (table.title or "")


def test_create_table_with_snapshot_time():
    """Table title includes the snapshot time when provided."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    table = display._create_table(snapshot_time="12:34:56")
    assert "12:34:56" in (table.title or "")


def test_create_table_first_column_is_row_index():
    """Sensors table has a # column for keyboard row shortcuts."""
    display = DisplayManager(StateManager(), update_interval=1.0)
    table = display._create_table()
    assert table.columns[0].header == "#"


def test_trigger_rerun_if_digit_sets_matching_event():
    """Digit 1 signals the first row's re-run event when present in ``_rerun_events``."""
    ev = asyncio.Event()
    display = DisplayManager(StateManager(), update_interval=1.0)
    display._runner_display_order = ["eslint", "tests"]
    display._rerun_events = {"eslint": ev}
    display._trigger_rerun_if_digit("1")
    assert ev.is_set()


def test_trigger_rerun_records_started_at_only_for_triggered_mode():
    """``_triggered_run_started_at`` is set for digit re-run only when Repeat column is trigger."""
    ev = asyncio.Event()
    display = DisplayManager(StateManager(), update_interval=1.0)
    display._runner_display_order = ["tr", "iv"]
    display._rerun_events = {"tr": ev, "iv": ev}
    display._runner_modes = {"tr": "trigger", "iv": "30s"}
    display._trigger_rerun_if_digit("1")
    assert "tr" in display._triggered_run_started_at
    ev.clear()
    display._trigger_rerun_if_digit("2")
    assert "iv" not in display._triggered_run_started_at


def test_last_run_finished_trigger_compare():
    """Completion is detected when persisted lastRun is on/after the trigger timestamp."""
    t0 = datetime.now()
    t1 = t0 + timedelta(seconds=1)
    assert DisplayManager._last_run_finished_trigger(t1, t0)
    assert not DisplayManager._last_run_finished_trigger(t0, t1)


@pytest.mark.asyncio
async def test_handle_key_s_sets_snapshot_event():
    """Pressing S sets the snapshot event."""
    events = DisplayEvents()
    display = DisplayManager(StateManager(), events=events)
    await display._handle_key("s")
    assert events.snapshot.is_set()
    assert not events.clear.is_set()
    assert not events.shutdown.is_set()


@pytest.mark.asyncio
async def test_handle_key_c_sets_clear_event():
    """Pressing C sets the clear event."""
    events = DisplayEvents()
    display = DisplayManager(StateManager(), events=events)
    await display._handle_key("c")
    assert events.clear.is_set()
    assert display._clear_status == "Clearing..."
    assert not events.snapshot.is_set()
    assert not events.shutdown.is_set()


@pytest.mark.asyncio
async def test_handle_key_q_sets_shutdown_event():
    """Pressing Q sets the shutdown event."""
    events = DisplayEvents()
    display = DisplayManager(StateManager(), events=events)
    await display._handle_key("q")
    assert events.shutdown.is_set()
    assert not events.snapshot.is_set()
    assert not events.clear.is_set()


@pytest.mark.asyncio
async def test_handle_key_w_stops_display():
    """Pressing W closes the viewer without signaling shutdown."""
    events = DisplayEvents()
    display = DisplayManager(StateManager(), events=events)
    await display._handle_key("w")
    assert display._should_stop
    assert not events.shutdown.is_set()


@pytest.mark.asyncio
async def test_handle_key_attach_mode_delegates_to_callbacks():
    """In attach mode, action keys invoke RPC callbacks instead of local events."""
    events = DisplayEvents()
    on_snapshot = AsyncMock()
    on_clear = AsyncMock()
    on_rerun = AsyncMock()
    on_shutdown = AsyncMock()
    display = DisplayManager(
        StateManager(),
        events=events,
        attach=True,
        on_snapshot=on_snapshot,
        on_clear=on_clear,
        on_rerun=on_rerun,
        on_shutdown=on_shutdown,
    )
    display._runner_display_order = ["eslint"]

    await display._handle_key("s")
    await display._handle_key("c")
    await display._handle_key("1")
    await display._handle_key("q")

    on_snapshot.assert_awaited_once()
    on_clear.assert_awaited_once()
    on_rerun.assert_awaited_once_with("eslint")
    on_shutdown.assert_awaited_once()
    assert not events.snapshot.is_set()
    assert not events.clear.is_set()
    assert not events.shutdown.is_set()
    assert display._should_stop


def test_runner_row_cells_on_check():
    """on_check mode returns a static hint in the details cell."""
    display = DisplayManager(StateManager())
    from sensors.persistence.models import StateEntry

    state = StateEntry(lastUpdated=datetime.now(), runners={})
    _, _, _, details = display._runner_row_cells("ruff", state, on_check=True)
    assert "Runs on" in details
    assert "sensors check" in details


@pytest.mark.asyncio
async def test_populate_table_on_check_runner():
    """on_check runners appear in the table with the on_check mode label."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfgs = [
            RunnerConfig(
                name="ruff",
                parser="ruff",
                enabled=True,
                mode=RunnerMode.ON_CHECK,
                command="ruff check",
            ),
        ]
        display = DisplayManager(sm, runner_configs=cfgs)
        table = display._create_table()
        state = await sm.read_state()
        await display._populate_table(table, state=state)
        assert table.row_count == 1
        assert display._runner_modes["ruff"] == "on_check"


@pytest.mark.asyncio
async def test_populate_table_extra_runner_not_in_config():
    """Runners in state but not in config still appear in the table."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        now = datetime.now()
        await sm.update_state(
            "orphan",
            RunnerEntry(
                lastRun=now,
                status="success",
                reading=SensorReading(
                    success=True,
                    summary="ok",
                    score=ScoreInfo(value=0, direction="less"),
                ),
            ),
        )
        display = DisplayManager(sm, runner_configs=[])
        table = display._create_table()
        await display._populate_table(table)
        buf = StringIO()
        Console(file=buf, force_terminal=True, width=120).print(table)
        assert "orphan" in buf.getvalue()


@pytest.mark.asyncio
async def test_populate_table_triggered_running_overlay():
    """Digit-triggered runner shows Running… until state catches up."""
    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        cfgs = [
            RunnerConfig(
                name="lint",
                parser="eslint",
                enabled=True,
                mode=RunnerMode.TRIGGERED,
                command="eslint .",
            ),
        ]
        display = DisplayManager(sm, runner_configs=cfgs)
        display._triggered_run_started_at["lint"] = datetime.now()
        table = display._create_table()
        await display._populate_table(table)
        buf = StringIO()
        Console(file=buf, force_terminal=True, width=120).print(table)
        assert "Running…" in buf.getvalue()


@pytest.mark.asyncio
async def test_populate_table_no_runners_active():
    """Empty config and empty state show a placeholder row."""
    from sensors.persistence.models import StateEntry

    with tempfile.TemporaryDirectory() as tmpdir:
        sm = StateManager(Path(tmpdir) / "state.json")
        display = DisplayManager(sm, runner_configs=[])
        table = MagicMock()
        state = StateEntry(lastUpdated=datetime.now(), runners={})
        await display._populate_table(table, state=state)
        table.add_row.assert_called_once_with(
            "", "[dim]No runners active[/dim]", "", "", "", "", ""
        )


@pytest.mark.asyncio
async def test_keypress_s_triggers_snapshot_in_run_loop():
    """Run loop sets snapshot event when S is pressed, without calling save_snapshot directly."""
    events = DisplayEvents()
    sm = StateManager()

    with (
        patch("sensors.tui.display.tty"),
        patch("sensors.tui.display.termios"),
        patch("sensors.tui.display.sys"),
        patch("sensors.tui.display.os.read", return_value=b"s"),
        patch("sensors.tui.display.select.select", return_value=([True], [], [])),
    ):
        sm.read_state = AsyncMock(return_value=AsyncMock(runners={}, snapshot=None, queryLog=[]))
        display = DisplayManager(sm, events=events, update_interval=0.01)
        display._should_stop = False

        async def stop_after_event():
            await asyncio.wait_for(events.snapshot.wait(), timeout=1.0)
            display.stop()

        await asyncio.gather(display.run(), stop_after_event())

    assert events.snapshot.is_set()
