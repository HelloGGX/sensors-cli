"""Main orchestrator: loads config, runs enabled runners, manages lifecycle."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from datetime import datetime
from pathlib import Path
from typing import Literal

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
from sensors.tui.display import DisplayEvents, DisplayManager

logger = logging.getLogger(__name__)

DisplayMode = Literal["none", "inline"]


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
        needs_rerun = runner_config.mode in (RunnerMode.INTERVAL, RunnerMode.TRIGGERED)
        ev: asyncio.Event | None = asyncio.Event() if needs_rerun else None
        r = _runner_factory(runner_config, state_manager, rerun_event=ev)
        if r is not None:
            runners.append(r)
            if ev is not None:
                rerun_events[runner_config.name] = ev
    return runners, rerun_events


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


async def run_sensors(
    working_dir: str,
    config_name: str | None = None,
    *,
    display_mode: DisplayMode = "inline",
) -> None:
    """Load config, start control socket, start runners, run until shutdown."""
    try:
        config = await load_config(working_dir, config_name)
    except ConfigLoadError as e:
        print(f"Config error: {e}")
        raise

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

    display: DisplayManager | None = None
    display_task: asyncio.Task[None] | None = None
    if display_mode == "inline":
        display = DisplayManager(
            state_manager,
            runner_configs=config.runners,
            update_interval=1.0,
            events=events,
            attach=False,
        )
        display_task = asyncio.create_task(display.run())

    runners: list[GenericRunner] = []
    runner_tasks: list[asyncio.Task] = []
    rerun_events: dict[str, asyncio.Event] = {}

    try:
        while True:
            runners, rerun_events = _build_runners(config, state_manager)
            events.rerun_events = rerun_events
            if display is not None:
                display.set_runner_configs(
                    config.runners,
                    active_runner_names={r.config.name for r in runners},
                    rerun_events=rerun_events,
                )
            if not runners:
                print("No enabled runners. Exiting.")
                break

            runner_tasks = [asyncio.create_task(r.start()) for r in runners]
            clear_wait = asyncio.create_task(events.clear.wait())
            shutdown_wait = asyncio.create_task(events.shutdown.wait())
            snapshot_wait = asyncio.create_task(events.snapshot.wait())

            wait_on: list[asyncio.Task] = (
                runner_tasks + [clear_wait, shutdown_wait, snapshot_wait]
            )

            done, _ = await asyncio.wait(
                wait_on,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if snapshot_wait in done:
                events.snapshot.clear()
                await state_manager.save_snapshot()
                now = datetime.now().strftime("%H:%M:%S")
                if display is not None:
                    display._snapshot_status = f"Snapshot saved at {now}"
                clear_wait.cancel()
                shutdown_wait.cancel()
                continue

            if shutdown_wait in done:
                snapshot_wait.cancel()
                if clear_wait in done and not clear_wait.cancelled():
                    clear_wait.cancel()
                print("\nShutting down sensors...")
                break

            if clear_wait in done:
                events.clear.clear()
                await _stop_runners(runners, runner_tasks)
                await state_manager.reset()
                try:
                    config = await load_config(working_dir, config_name)
                except ConfigLoadError as e:
                    print(f"Config reload error: {e}")
                    logger.error("Config reload failed, shutting down: %s", e)
                    break
                if display is not None:
                    display._clear_status = None
                    display._snapshot_status = None
                continue

            # One or more runner tasks exited unexpectedly (not via shutdown/clear).
            # Log which ones finished and any exceptions they raised.
            for task in done:
                if task in runner_tasks:
                    exc = task.exception() if not task.cancelled() else None
                    runner_name = getattr(
                        runners[runner_tasks.index(task)].config, "name", "unknown"
                    )
                    if exc is not None:
                        logger.error(
                            "Runner '%s' exited unexpectedly with an exception -- "
                            "halting sensors",
                            runner_name,
                            exc_info=exc,
                        )
                    else:
                        logger.error(
                            "Runner '%s' exited unexpectedly (no exception) -- "
                            "halting sensors",
                            runner_name,
                        )
            await _stop_runners(runners, runner_tasks)
            break
    finally:
        if runners and runner_tasks:
            await _stop_runners(runners, runner_tasks)

        if display is not None:
            display.stop()
        if display_task is not None and not display_task.done():
            display_task.cancel()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    asyncio.gather(display_task, return_exceptions=True),
                    timeout=2.0
                )

        control_server.close()
        await control_server.wait_closed()
        remove_control_artifacts(ctl_path, sock_path)
