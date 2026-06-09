"""Main orchestrator: loads config, runs enabled runners, manages lifecycle."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from sensors.config.loader import (
    ConfigLoadError,
    configure_file_logging,
    control_path_for_config,
    load_config,
    log_path_for_config,
    resolve_config_path,
    socket_path_for_config,
    state_path_for_config,
)
from sensors.config.schema import RunnerConfig, RunnerMode, SensorsConfig
from sensors.events import DisplayEvents
from sensors.orchestration.control_server import (
    control_state,
    ensure_no_live_sensors,
    remove_control_artifacts,
    start_control_server,
    write_control_file,
)
from sensors.persistence.state_manager import StateManager
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers import ParserRegistry

logger = logging.getLogger(__name__)


class OrchestratorEventType(Enum):
    """First-completed wait result in the main orchestration loop."""

    SNAPSHOT = auto()
    SHUTDOWN = auto()
    CLEAR = auto()
    RUNNER_EXIT = auto()


@dataclass
class OrchestratorContext:
    """Mutable state for one `run_sensors` invocation."""

    working_dir: str
    config_name: str | None
    config: SensorsConfig
    config_path: Path
    ctl_path: Path
    sock_path: Path
    state_manager: StateManager
    events: DisplayEvents
    control_server: asyncio.AbstractServer
    runners: list[GenericRunner] = field(default_factory=list)
    runner_tasks: list[asyncio.Task] = field(default_factory=list)


@dataclass
class RunnerBatch:
    """One iteration of started runners and orchestrator wait tasks."""

    runners: list[GenericRunner]
    runner_tasks: list[asyncio.Task]
    rerun_events: dict[str, asyncio.Event]
    clear_wait: asyncio.Task[None]
    shutdown_wait: asyncio.Task[None]
    snapshot_wait: asyncio.Task[None]


def _runner_factory(
    config: RunnerConfig,
    state_manager: StateManager,
    *,
    rerun_event: asyncio.Event | None,
) -> GenericRunner | None:
    """Create a generic runner instance with the appropriate parser plugin."""
    if not config.enabled:
        return None

    # on_check runners are executed synchronously by `sensors check`, not by the background loop.
    if config.mode == RunnerMode.ON_CHECK:
        return None

    try:
        parser_class = ParserRegistry.get(config.parser)
        parser = parser_class()
        return GenericRunner(config, parser, state_manager, rerun_event=rerun_event)
    except KeyError as e:
        msg = f"Warning: {e}. Skipping runner '{config.name}'."
        print(msg)
        logger.warning(msg)
        return None


def _add_runner_from_config(
    runner_config: RunnerConfig,
    state_manager: StateManager,
    runners: list[GenericRunner],
    rerun_events: dict[str, asyncio.Event],
) -> None:
    """Append one background runner and optional rerun event from config."""
    needs_rerun = runner_config.mode in (RunnerMode.INTERVAL, RunnerMode.TRIGGERED)
    ev: asyncio.Event | None = asyncio.Event() if needs_rerun else None
    runner = _runner_factory(runner_config, state_manager, rerun_event=ev)
    if runner is None:
        return
    runners.append(runner)
    if ev is not None:
        rerun_events[runner_config.name] = ev


def _build_runners(
    config: SensorsConfig,
    state_manager: StateManager,
) -> tuple[list[GenericRunner], dict[str, asyncio.Event]]:
    """Create runner instances from config.

    Returns runners and a map of runner name -> asyncio.Event for keyboard/TUI re-runs
    (interval and triggered modes only).
    """
    runners: list[GenericRunner] = []
    rerun_events: dict[str, asyncio.Event] = {}
    for runner_config in config.runners:
        _add_runner_from_config(runner_config, state_manager, runners, rerun_events)
    return runners, rerun_events


def _wire_runner_batch(
    runner_tasks: list[asyncio.Task],
    events: DisplayEvents,
) -> RunnerBatch:
    """Build wait tasks for an orchestrator loop iteration (tests and production)."""
    return RunnerBatch(
        runners=[],
        runner_tasks=runner_tasks,
        rerun_events={},
        clear_wait=asyncio.create_task(events.clear.wait()),
        shutdown_wait=asyncio.create_task(events.shutdown.wait()),
        snapshot_wait=asyncio.create_task(events.snapshot.wait()),
    )


def _cancel_batch_waits(batch: RunnerBatch) -> None:
    """Cancel orchestrator wait tasks; ignore if already done."""
    for wait in (batch.clear_wait, batch.shutdown_wait, batch.snapshot_wait):
        if not wait.done():
            wait.cancel()


async def _stop_runners(runners: list[GenericRunner], runner_tasks: list[asyncio.Task]) -> None:
    """Stop all runners and cancel their tasks gracefully."""
    for r in runners:
        await r.stop()

    try:
        await asyncio.wait_for(
            asyncio.gather(*runner_tasks, return_exceptions=True),
            timeout=3.0
        )
    except asyncio.TimeoutError:
        for t in runner_tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*runner_tasks, return_exceptions=True)


def _cleanup_stale_control(control_path: Path, socket_path: Path) -> None:
    """Remove control artifacts if PID in file is dead."""
    data = control_state(control_path)
    if not data:
        return
    pid = data.get("pid")
    if isinstance(pid, int):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            remove_control_artifacts(control_path, socket_path)


async def _setup_orchestrator(
    working_dir: str,
    config_name: str | None,
) -> OrchestratorContext:
    """Load config, control plane, and signals."""
    config = await load_config(working_dir, config_name)
    config_path = resolve_config_path(working_dir, config_name)
    state_file = state_path_for_config(config_path)
    ctl_path = control_path_for_config(config_path)
    sock_path = socket_path_for_config(config_path)
    log_path = log_path_for_config(config_path)
    configure_file_logging(log_path)

    _cleanup_stale_control(ctl_path, sock_path)
    await ensure_no_live_sensors(ctl_path, sock_path)

    state_manager = StateManager(state_file)
    events = DisplayEvents()

    def signal_handler() -> None:
        events.shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    control_server = await start_control_server(sock_path, events)
    write_control_file(ctl_path, sock_path, os.getpid(), config_path.name, working_dir=working_dir)

    return OrchestratorContext(
        working_dir=working_dir,
        config_name=config_name,
        config=config,
        config_path=config_path,
        ctl_path=ctl_path,
        sock_path=sock_path,
        state_manager=state_manager,
        events=events,
        control_server=control_server,
    )


async def _start_runner_batch(ctx: OrchestratorContext) -> RunnerBatch | None:
    """Build runners and start runner tasks. None if no enabled runners."""
    runners, rerun_events = _build_runners(ctx.config, ctx.state_manager)
    ctx.events.rerun_events = rerun_events
    if not runners:
        return None

    runner_tasks = [asyncio.create_task(r.start()) for r in runners]
    batch = _wire_runner_batch(runner_tasks, ctx.events)
    batch.runners = runners
    batch.rerun_events = rerun_events
    return batch


def _cancel_wait_if_pending(wait: asyncio.Task) -> None:
    if not wait.done():
        wait.cancel()


def _cancel_waits_after_snapshot(batch: RunnerBatch) -> None:
    _cancel_wait_if_pending(batch.clear_wait)
    _cancel_wait_if_pending(batch.shutdown_wait)


def _cancel_waits_after_shutdown(batch: RunnerBatch, done: set[asyncio.Task]) -> None:
    _cancel_wait_if_pending(batch.snapshot_wait)
    if batch.clear_wait in done and not batch.clear_wait.cancelled():
        batch.clear_wait.cancel()


def _classify_orchestrator_event(
    batch: RunnerBatch,
    done: set[asyncio.Task],
) -> OrchestratorEventType:
    if batch.snapshot_wait in done:
        _cancel_waits_after_snapshot(batch)
        return OrchestratorEventType.SNAPSHOT
    if batch.shutdown_wait in done:
        _cancel_waits_after_shutdown(batch, done)
        return OrchestratorEventType.SHUTDOWN
    if batch.clear_wait in done:
        return OrchestratorEventType.CLEAR
    return OrchestratorEventType.RUNNER_EXIT


async def _wait_for_orchestrator_event(
    batch: RunnerBatch,
) -> tuple[OrchestratorEventType, set[asyncio.Task]]:
    """Wait until snapshot, shutdown, clear, or a runner task completes."""
    wait_on: list[asyncio.Task] = (
        batch.runner_tasks
        + [batch.clear_wait, batch.shutdown_wait, batch.snapshot_wait]
    )
    done, _ = await asyncio.wait(
        wait_on,
        return_when=asyncio.FIRST_COMPLETED,
    )
    return _classify_orchestrator_event(batch, done), done


async def _on_snapshot(ctx: OrchestratorContext, batch: RunnerBatch) -> None:
    ctx.events.snapshot.clear()
    await ctx.state_manager.save_snapshot()


async def _on_clear(ctx: OrchestratorContext, batch: RunnerBatch) -> bool:
    """Handle clear: stop runners, reset state, reload config. False = exit loop."""
    ctx.events.clear.clear()
    await _stop_runners(batch.runners, batch.runner_tasks)
    await ctx.state_manager.reset()
    try:
        ctx.config = await load_config(ctx.working_dir, ctx.config_name)
    except ConfigLoadError as e:
        print(f"Config reload error: {e}")
        logger.error("Config reload failed, shutting down: %s", e)
        return False
    return True


def _log_unexpected_runner_exits(
    done: set[asyncio.Task],
    runner_tasks: list[asyncio.Task],
    runners: list[GenericRunner],
) -> None:
    for task in done:
        if task not in runner_tasks:
            continue
        exc = task.exception() if not task.cancelled() else None
        runner_name = getattr(
            runners[runner_tasks.index(task)].config, "name", "unknown"
        )
        if exc is not None:
            logger.error(
                "Runner '%s' exited unexpectedly with an exception -- halting sensors",
                runner_name,
                exc_info=exc,
            )
        else:
            logger.error(
                "Runner '%s' exited unexpectedly (no exception) -- halting sensors",
                runner_name,
            )


async def _on_runner_exit(
    ctx: OrchestratorContext,
    batch: RunnerBatch,
    done: set[asyncio.Task],
) -> None:
    _log_unexpected_runner_exits(done, batch.runner_tasks, batch.runners)
    await _stop_runners(batch.runners, batch.runner_tasks)


async def _teardown_orchestrator(ctx: OrchestratorContext) -> None:
    if ctx.runners and ctx.runner_tasks:
        await _stop_runners(ctx.runners, ctx.runner_tasks)

    ctx.control_server.close()
    await ctx.control_server.wait_closed()
    remove_control_artifacts(ctx.ctl_path, ctx.sock_path)


async def run_sensors(
    working_dir: str,
    config_name: str | None = None,
) -> None:
    """Load config, start control socket, start runners, run until shutdown."""
    try:
        ctx = await _setup_orchestrator(working_dir, config_name)
    except ConfigLoadError as e:
        print(f"Config error: {e}")
        raise

    try:
        while True:
            batch = await _start_runner_batch(ctx)
            if batch is None:
                print("No enabled runners. Exiting.")
                break

            ctx.runners = batch.runners
            ctx.runner_tasks = batch.runner_tasks

            event, done = await _wait_for_orchestrator_event(batch)

            if event == OrchestratorEventType.SNAPSHOT:
                await _on_snapshot(ctx, batch)
                continue

            if event == OrchestratorEventType.SHUTDOWN:
                print("\nShutting down sensors...")
                break

            if event == OrchestratorEventType.CLEAR:
                if await _on_clear(ctx, batch):
                    continue
                break

            await _on_runner_exit(ctx, batch, done)
            break
    finally:
        await _teardown_orchestrator(ctx)
