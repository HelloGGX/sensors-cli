"""Live visual feedback display for the sensors."""

import asyncio
import os
import select
import sys
import termios
import tty
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from rich.console import Console
from rich.live import Live
from rich.table import Table

from sensors.config.schema import RunnerConfig
from sensors.persistence.state_manager import StateManager


@dataclass
class DisplayEvents:
    """Events used to signal actions from the display to the orchestrator."""

    clear: asyncio.Event = field(default_factory=asyncio.Event)
    shutdown: asyncio.Event = field(default_factory=asyncio.Event)
    snapshot: asyncio.Event = field(default_factory=asyncio.Event)
    #: Populated by orchestrator after runners are built; keyed by runner name.
    #: Control server uses this to trigger remote re-runs without touching the main loop.
    rerun_events: dict[str, asyncio.Event] = field(default_factory=dict)


class DisplayManager:
    """Manages live terminal display showing runner status."""

    def __init__(  # noqa: PLR0913
        self,
        state_manager: StateManager,
        runner_configs: list[RunnerConfig] | None = None,
        update_interval: float = 1.0,
        events: DisplayEvents | None = None,
        *,
        attach: bool = False,
        on_snapshot: Callable[[], Awaitable[None]] | None = None,
        on_clear: Callable[[], Awaitable[None]] | None = None,
        on_rerun: Callable[[str], Awaitable[None]] | None = None,
        on_shutdown: Callable[[], Awaitable[None]] | None = None,
    ):
        """Initialize the display manager.

        Args:
            state_manager: State manager to read current state from
            runner_configs: List of runner configs (used to display mode info)
            update_interval: How often to refresh the display (seconds)
            events: Events used to signal actions to the orchestrator
            attach: If True, action keys delegate to callbacks (remote RPC) instead of
                setting local events. W always closes viewer; Q always stops sensors.
            on_snapshot: Async callback for S key in attach mode.
            on_clear: Async callback for C key in attach mode.
            on_rerun: Async callback for digit keys in attach mode; receives runner name.
            on_shutdown: Async callback for Q key in attach mode.
        """
        self.state_manager = state_manager
        self.update_interval = update_interval
        self.console = Console()
        self._should_stop = False
        self._snapshot_status: str | None = None
        self._events = events or DisplayEvents()
        self._clear_status: str | None = None
        self._runner_modes: dict[str, str] = {}
        self._runner_enabled: dict[str, bool] = {}
        self._active_runner_names: set[str] | None = None
        self._runner_display_order: list[str] = []
        self._rerun_events: dict[str, asyncio.Event] = {}
        #: Local wall time when the user triggered a ``mode: triggered`` runner (digit key); cleared
        #: when persisted ``lastRun`` is >= this time so Details can show "Running…" immediately.
        self._triggered_run_started_at: dict[str, datetime] = {}
        self._attach = attach
        self._on_snapshot = on_snapshot
        self._on_clear = on_clear
        self._on_rerun = on_rerun
        self._on_shutdown = on_shutdown
        self.set_runner_configs(runner_configs)

    def set_runner_configs(
        self,
        runner_configs: list[RunnerConfig] | None,
        *,
        active_runner_names: set[str] | None = None,
        rerun_events: dict[str, asyncio.Event] | None = None,
    ) -> None:
        """Update the runner mode display info from configs.

        ``active_runner_names`` is the set of runner names that actually got a GenericRunner
        (enabled + parser registered). Enabled runners not in this set are skipped at startup
        (e.g. stale ``uv tool install`` missing a parser) — the table shows that explicitly
        instead of endless \"Waiting to start...\".

        ``rerun_events`` maps runner name to the asyncio.Event used for digit-key re-runs
        (interval/triggered modes in the main sensors process only).
        """
        self._runner_modes = {}
        self._runner_enabled = {}
        self._on_check_runners: set[str] = set()
        self._active_runner_names = active_runner_names
        self._rerun_events = dict(rerun_events) if rerun_events else {}
        self._triggered_run_started_at = {}
        if runner_configs:
            self._runner_display_order = [rc.name for rc in runner_configs]
            for rc in runner_configs:
                self._runner_enabled[rc.name] = rc.enabled
                if rc.mode == "watch":
                    self._runner_modes[rc.name] = "watch"
                elif rc.mode == "triggered":
                    self._runner_modes[rc.name] = "trigger"
                elif rc.mode == "on_check":
                    self._runner_modes[rc.name] = "on_check"
                    self._on_check_runners.add(rc.name)
                elif rc.interval:
                    secs = rc.interval / 1000
                    self._runner_modes[rc.name] = f"{secs:g}s"
                else:
                    self._runner_modes[rc.name] = "interval"
        else:
            self._runner_display_order = []

    def _format_time_ago(self, timestamp: datetime) -> str:
        """Format a timestamp as 'X seconds/minutes ago'."""
        now = datetime.now().replace(tzinfo=timestamp.tzinfo)
        delta = now - timestamp
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return f"{seconds}s ago"
        elif seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m ago"
        else:
            hours = seconds // 3600
            return f"{hours}h ago"

    def _format_time_short(self, timestamp: datetime) -> str:
        """Format a timestamp as HH:MM:SS in the user's local timezone."""
        from sensors.time_util import format_local_short

        return format_local_short(timestamp)

    _STATUS_ICONS: dict[str, str] = {
        "success": "🟢",
        "failure": "🔴",
        "below_threshold": "🟡",
    }

    def _get_status_icon(self, status: str) -> str:
        """Get a colored status icon for a runner status."""
        return self._STATUS_ICONS.get(status, "[dim]?[/dim]")

    def _get_trend_indicator(self, runner_name: str, current_score, snapshot) -> str:
        """Get an emoji trend indicator comparing current score to snapshot."""
        if current_score is None or snapshot is None:
            return "➖"
        snap_runner = snapshot.runners.get(runner_name)
        if snap_runner is None or snap_runner.score is None:
            return "➖"

        cur = current_score.value
        snap = snap_runner.score.value
        direction = current_score.direction

        if cur == snap:
            return "➡️"

        improving = (cur < snap and direction == "less") or (cur > snap and direction == "more")

        if improving:
            return "🚀"
        else:
            return "🔺"

    @staticmethod
    def _last_run_finished_trigger(last_run: datetime, started: datetime) -> bool:
        """True if persisted last run is from on/after the trigger (command finished)."""
        lr = last_run.replace(tzinfo=None) if last_run.tzinfo else last_run
        st = started.replace(tzinfo=None) if started.tzinfo else started
        return lr >= st

    def _maybe_clear_triggered_running(self, state, runner_name: str) -> bool:
        """Drop trigger overlay if state shows a new result; return True if still running."""
        started = self._triggered_run_started_at.get(runner_name)
        if started is None:
            return False
        rs = state.runners.get(runner_name)
        if rs is not None and self._last_run_finished_trigger(rs.lastRun, started):
            del self._triggered_run_started_at[runner_name]
            return False
        return True

    def _get_score_delta(self, runner_name: str, current_score, snapshot) -> tuple:
        """Get a formatted score delta and whether it's improving.

        Returns (delta_str, improving) or ("", None) if no comparison available.
        """
        if current_score is None or snapshot is None:
            return "", None
        snap_runner = snapshot.runners.get(runner_name)
        if snap_runner is None or snap_runner.score is None:
            return "", None

        cur = current_score.value
        snap = snap_runner.score.value
        if cur == snap:
            return "", None

        # +/- reflects actual numeric change
        diff = cur - snap
        delta_str = f"(+{diff})" if diff > 0 else f"({diff})"
        improving = (diff < 0 and current_score.direction == "less") or \
                    (diff > 0 and current_score.direction == "more")
        return delta_str, improving

    def _create_table(self, snapshot_time: str | None = None) -> Table:
        """Create a Rich table showing current runner status."""
        title = "[bold]Sensors Status[/bold]"
        if snapshot_time:
            title += f"  [dim]snapshot {snapshot_time}[/dim]"
        table = Table(title=title, show_header=True, header_style="bold cyan")
        table.add_column("#", width=3, justify="right")
        table.add_column("Sensor", style="white", width=12)
        table.add_column("When", width=7)
        table.add_column("St", width=3)
        table.add_column("Trend", width=6, justify="center")
        table.add_column("Last Run", width=12)
        table.add_column("Details", min_width=20)

        return table

    async def _populate_table(self, table: Table, state=None) -> None:
        """Populate the table with current state data.

        Shows all configured runners, even those that haven't reported yet.
        """
        try:
            if state is None:
                state = await self.state_manager.read_state()

            all_runner_names = list(self._runner_modes.keys())
            if not all_runner_names and not state.runners:
                table.add_row("[dim]No runners active[/dim]", "", "", "", "", "", "")
                return

            shown = set()
            for row_index, runner_name in enumerate(all_runner_names):
                shown.add(runner_name)
                mode = self._runner_modes.get(runner_name, "?")
                num_cell = str(row_index + 1)

                triggered_running = self._maybe_clear_triggered_running(state, runner_name)

                if runner_name in self._on_check_runners:
                    status_icon = "[dim]·[/dim]"
                    trend = ""
                    last_run = ""
                    details = "[dim]Runs on `sensors check`[/dim]"
                elif triggered_running:
                    status_icon = "[dim]⏳[/dim]"
                    details = "[dim]⏳ Running…[/dim]"
                    trend = "➖"
                    if runner_name in state.runners:
                        runner_state = state.runners[runner_name]
                        last_run = self._format_time_ago(runner_state.lastRun)
                    else:
                        last_run = ""
                elif runner_name in state.runners:
                    runner_state = state.runners[runner_name]
                    status_icon = self._get_status_icon(runner_state.status)
                    last_run = self._format_time_ago(runner_state.lastRun)
                    details = runner_state.formatted.details_terminal or "[dim]No details[/dim]"
                    delta_str, improving = self._get_score_delta(runner_name, runner_state.score, state.snapshot)
                    if delta_str:
                        color = "green" if improving else "red"
                        details = f"{details} [{color}]{delta_str}[/{color}]"
                    trend = self._get_trend_indicator(runner_name, runner_state.score, state.snapshot)
                else:
                    last_run = ""
                    trend = ""
                    if not self._runner_enabled.get(runner_name, True):
                        status_icon = "[dim]⊘[/dim]"
                        details = "[dim]Disabled (enabled: false)[/dim]"
                    elif (
                        self._active_runner_names is not None
                        and runner_name not in self._active_runner_names
                    ):
                        status_icon = "[yellow]![/yellow]"
                        details = (
                            "[yellow]Not running — parser not loaded (reinstall sensors CLI?) "
                            "or runner skipped at startup. Check console for warnings.[/yellow]"
                        )
                    else:
                        status_icon = "[dim]⏳[/dim]"
                        details = "[dim]Waiting to start...[/dim]"

                table.add_row(
                    num_cell,
                    runner_name,
                    f"[dim]{mode}[/dim]",
                    status_icon,
                    trend,
                    last_run,
                    details
                )

            extra_offset = len(all_runner_names)
            j = 0
            for runner_name, runner_state in state.runners.items():
                if runner_name in shown:
                    continue
                mode = self._runner_modes.get(runner_name, "?")
                triggered_running = self._maybe_clear_triggered_running(state, runner_name)
                if triggered_running:
                    status_icon = "[dim]⏳[/dim]"
                    details = "[dim]⏳ Running…[/dim]"
                    trend = "➖"
                    last_run = self._format_time_ago(runner_state.lastRun)
                else:
                    status_icon = self._get_status_icon(runner_state.status)
                    last_run = self._format_time_ago(runner_state.lastRun)
                    details = runner_state.formatted.details_terminal or "[dim]No details[/dim]"
                    delta_str, improving = self._get_score_delta(runner_name, runner_state.score, state.snapshot)
                    if delta_str:
                        color = "green" if improving else "red"
                        details = f"{details} [{color}]{delta_str}[/{color}]"
                    trend = self._get_trend_indicator(runner_name, runner_state.score, state.snapshot)
                num_cell = str(extra_offset + j + 1)
                j += 1
                table.add_row(
                    num_cell,
                    runner_name,
                    f"[dim]{mode}[/dim]",
                    status_icon,
                    trend,
                    last_run,
                    details
                )
        except Exception as e:
            table.add_row(f"[red]Error: {e}[/red]", "", "", "", "", "", "")

    def _check_keypress(self, fd: int) -> str | None:
        """Non-blocking check for a single keypress. Returns the char or None."""
        if select.select([sys.stdin], [], [], 0)[0]:
            return os.read(fd, 1).decode("utf-8", errors="ignore")
        return None

    def _trigger_rerun_if_digit(self, ch: str | None) -> None:
        """If ``ch`` is 1–9, signal the corresponding runner's re-run event (if any)."""
        if not ch or ch not in "123456789":
            return
        idx = int(ch) - 1
        if idx >= len(self._runner_display_order):
            return
        name = self._runner_display_order[idx]
        ev = self._rerun_events.get(name)
        if ev is not None:
            ev.set()
            if self._runner_modes.get(name) == "trigger":
                self._triggered_run_started_at[name] = datetime.now()

    async def run(self) -> None:
        """Run the live display until stopped."""
        from rich.console import Group
        from rich.text import Text

        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)

        try:
            # cbreak + no echo: immediate char reads, nothing printed
            tty.setcbreak(fd)
            new_settings = termios.tcgetattr(fd)
            new_settings[3] = new_settings[3] & ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, new_settings)

            with Live(self._create_table(), console=self.console, refresh_per_second=1) as live:
                while not self._should_stop:
                    # Check for keypress (non-blocking)
                    try:
                        ch = self._check_keypress(fd)
                        if ch:
                            ch_lower = ch.lower()
                            if ch_lower == "w":
                                self._should_stop = True
                            elif ch_lower == "q":
                                if self._attach:
                                    if self._on_shutdown is not None:
                                        await self._on_shutdown()
                                    self._should_stop = True
                                else:
                                    self._events.shutdown.set()
                            elif ch_lower == "s":
                                if self._attach and self._on_snapshot is not None:
                                    await self._on_snapshot()
                                else:
                                    self._events.snapshot.set()
                            elif ch_lower == "c":
                                if self._attach:
                                    if self._on_clear is not None:
                                        await self._on_clear()
                                else:
                                    self._clear_status = "Clearing..."
                                    self._events.clear.set()
                            elif ch in "123456789":
                                if self._attach:
                                    idx = int(ch) - 1
                                    if idx < len(self._runner_display_order) and self._on_rerun is not None:
                                        await self._on_rerun(self._runner_display_order[idx])
                                else:
                                    self._trigger_rerun_if_digit(ch)
                    except OSError:
                        pass

                    state = await self.state_manager.read_state()
                    snapshot_time = self._format_time_short(state.snapshot.timestamp) if state.snapshot else None
                    table = self._create_table(snapshot_time)
                    await self._populate_table(table, state)

                    # Status bar with snapshot info and keyboard hints
                    hint = (
                        "[dim]Press [bold]1[/bold]-[bold]9[/bold] re-run row  "
                        "[bold]S[/bold] snapshot  [bold]C[/bold] clear & restart  "
                        "[bold]W[/bold] close viewer  [bold]Q[/bold] quit and stop sensors[/dim]"
                    )
                    status_parts = [hint]
                    if self._snapshot_status:
                        status_parts.append(f"[green]{self._snapshot_status}[/green]")
                    if self._clear_status:
                        status_parts.append(f"[yellow]{self._clear_status}[/yellow]")
                    status_line = Text.from_markup("  │  ".join(status_parts))

                    display_group = Group(table, status_line)
                    live.update(display_group)

                    await asyncio.sleep(self.update_interval)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

    def stop(self) -> None:
        """Stop the display manager."""
        self._should_stop = True
