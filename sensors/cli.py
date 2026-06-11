"""CLI entry point: start, show, snapshot, status, check, stop, refresh.

``status`` reports whether the sensors process is running; ``check`` prints runner results."""

from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import typer

from sensors.config.loader import (
    ConfigLoadError,
    control_path_for_config,
    history_path_for_config,
    load_config_sync,
    resolve_config_path,
    socket_path_for_config,
    state_path_for_config,
)
from sensors.config.schema import RunnerConfig, SensorsConfig
from sensors.events import DisplayEvents
from sensors.orchestration.control_server import (
    control_state,
    is_sensors_running,
    rpc_unix,
    rpc_unix_sync,
    stop_running_sensors,
)
from sensors.orchestration.orchestrator import run_sensors
from sensors.persistence.models import (
    HistoryEntry,
    HistoryRunnerEntry,
    RunnerEntry,
    SnapshotEntry,
    StateEntry,
)
from sensors.persistence.state_manager import StateManager
from sensors.time_util import seconds_ago as _seconds_ago
from sensors.tui.display import DisplayManager

app = typer.Typer(help="Sensors: continuous code quality monitoring for coding agents")
_state_managers: dict[tuple[str, str | None], StateManager] = {}



def _format_runner_dir(working_dir: str, workspace_root: str | None) -> str | None:
    """Return a display string for a runner's working dir, or None if it should be omitted."""
    if workspace_root is not None and working_dir == workspace_root:
        return None
    if workspace_root is not None:
        try:
            return str(Path(working_dir).relative_to(workspace_root))
        except ValueError:
            pass
    return working_dir


def _relativize_runner_configs(
    runner_configs: dict[str, RunnerConfig],
    workspace_root: str,
) -> dict[str, RunnerConfig]:
    """Return a copy of runner_configs with workingDir set to a display-friendly value."""
    import copy

    result = {}
    for name, rc in runner_configs.items():
        rc_copy = copy.copy(rc)
        if rc_copy.workingDir:
            rc_copy.workingDir = _format_runner_dir(rc_copy.workingDir, workspace_root)
        result[name] = rc_copy
    return result


def _runner_description(
    runner_cfg: RunnerConfig,
    runner_state: RunnerEntry | None = None,
) -> str:
    parts = [f"cmd: `{runner_cfg.command}`"]
    if runner_cfg.workingDir:
        parts.append(f"dir: {runner_cfg.workingDir}")
    if runner_cfg.result:
        parts.append(f"result: {runner_cfg.result}")
    if runner_cfg.commandTimeout is not None:
        parts.append(
            f"commandTimeout: {runner_cfg.commandTimeout}s (0 = no limit)"
        )
    score = runner_state.reading.score if (runner_state and runner_state.reading) else None
    if score is not None:
        direction = "lower is better" if score.direction == "less" else "higher is better"
        desc = score.description
        if desc:
            parts.append(f"score: {desc} ({direction})")
        else:
            parts.append(f"score: {score.value} ({direction})")
    return ", ".join(parts)


def _score_delta(runner_name: str, runner_state: RunnerEntry, snapshot: SnapshotEntry | None) -> str:
    cur_score = runner_state.reading.score if runner_state.reading else None
    if cur_score is None or snapshot is None:
        return ""
    snap_runner = snapshot.runners.get(runner_name)
    snap_score = snap_runner.reading.score if (snap_runner and snap_runner.reading) else None
    if snap_score is None:
        return ""
    cur = cur_score.value
    snap = snap_score.value
    if cur == snap:
        return "Same as snapshot"
    diff = cur - snap
    diff_str = f"(+{diff})" if diff > 0 else f"({diff})"
    improving = (
        (diff < 0 and cur_score.direction == "less")
        or (diff > 0 and cur_score.direction == "more")
    )
    label = "Better than snapshot" if improving else "Worse than snapshot"
    return f"{label} {diff_str}"


_NOT_RUNNING_SHOW = (
    "No sensors is running for this project. Start one with:\n"
    "  sensors start .\n"
    "or with the live display:\n"
    "  sensors start --show ."
)

_NOT_RUNNING_SNAPSHOT = (
    "No sensors is running for this project. Start one with:\n"
    "  sensors start .\n"
    "or:\n"
    "  sensors start --show ."
)


def _require_running_sensors(
    wd: str,
    config: str | None,
    *,
    not_running_message: str,
) -> tuple[Path, Path, dict]:
    """Resolve config, verify sensors running; return (cfg_path, socket_path, control_data)."""
    try:
        cfg_path = resolve_config_path(wd, config)
    except ConfigLoadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from None

    ctl = control_path_for_config(cfg_path)
    sock = socket_path_for_config(cfg_path)
    if not is_sensors_running(ctl, sock):
        typer.echo(not_running_message, err=True)
        raise typer.Exit(2)

    data = control_state(ctl)
    if not data or "socketPath" not in data:
        typer.echo("Invalid control file.", err=True)
        raise typer.Exit(2)

    return cfg_path, Path(data["socketPath"]), data


def _make_attach_callbacks(
    socket_path: Path,
    display_box: list[DisplayManager | None],
) -> tuple[
    Callable[[], Awaitable[None]],
    Callable[[], Awaitable[None]],
    Callable[[str], Awaitable[None]],
    Callable[[], Awaitable[None]],
]:
    async def on_snapshot() -> None:
        res = await rpc_unix(socket_path, "snapshot")
        d = display_box[0]
        if d is None:
            return
        if res.get("ok"):
            now = datetime.now().strftime("%H:%M:%S")
            d._snapshot_status = f"SnapshotEntry requested at {now}"
        else:
            d._snapshot_status = f"SnapshotEntry failed: {res.get('error', res)}"

    async def on_clear() -> None:
        res = await rpc_unix(socket_path, "clear")
        d = display_box[0]
        if d is not None:
            if res.get("ok"):
                d._clear_status = "Clear requested"
            else:
                d._clear_status = f"Clear failed: {res.get('error', res)}"

    async def on_rerun(runner_name: str) -> None:
        res = await rpc_unix(socket_path, "rerun", params={"name": runner_name})
        d = display_box[0]
        if d is not None and res.get("ok"):
            d._triggered_run_started_at[runner_name] = datetime.now()

    async def on_shutdown() -> None:
        await rpc_unix(socket_path, "shutdown")

    return on_snapshot, on_clear, on_rerun, on_shutdown


@dataclass
class CheckContext:
    """Loaded state and config for a ``sensors check`` run."""

    state: StateEntry
    sensors_config: SensorsConfig | None
    runner_configs: dict[str, RunnerConfig]
    on_check_configs: list[RunnerConfig]
    now: datetime


def _runner_status_text(rs: RunnerEntry) -> str:
    if rs.status == "success":
        return "SUCCESS"
    if rs.status == "below_threshold":
        return "SUCCESS (below threshold)"
    return "FAILURE"


def _check_exit_code(state: StateEntry) -> int:
    for rs in state.runners.values():
        if rs.status == "failure":
            return 1
    if any(rs.status == "below_threshold" for rs in state.runners.values()):
        return 3
    return 0


def _print_check_header(
    state: StateEntry,
    sensors_config: SensorsConfig | None,
    now: datetime,
) -> None:
    updated_ago = _seconds_ago(state.lastUpdated, now)
    print("SENSORS STATUS")
    if state.runners:
        print(f"Updated: {state.lastUpdated.isoformat().replace('+00:00', 'Z')} ({updated_ago})")
    print()
    if sensors_config and sensors_config.prompt:
        print(sensors_config.prompt)
        print()


def _print_runner_result(
    name: str,
    rs: RunnerEntry,
    runner_configs: dict[str, RunnerConfig],
    state: StateEntry,
    now: datetime,
) -> None:
    status_text = _runner_status_text(rs)
    details = (rs.reading.formatted.summary_llm if rs.reading else "") or "no details"
    delta = _score_delta(name, rs, state.snapshot)
    ran_ago = _seconds_ago(rs.lastRun, now)
    line = f"{name}: {status_text} ({details}) [ran {ran_ago}]"
    if delta:
        line += f" | {delta}"
    print(line)

    if name in runner_configs:
        print(f"  {_runner_description(runner_configs[name], rs)}")
        if runner_configs[name].prompt:
            print(f"  prompt: {runner_configs[name].prompt}")

    if rs.status == "failure":
        failures = rs.reading.formatted.failures_llm if rs.reading else ""
        if failures:
            print(failures)

    print()


async def _print_on_check_sections(
    on_check_configs: list[RunnerConfig],
    workspace_root: str | None = None,
) -> None:
    for rc in on_check_configs:
        output = await _run_on_check_command(rc)
        print(f"{rc.name}: on_check")
        dir_suffix = ""
        if rc.workingDir and (workspace_root is None or rc.workingDir != workspace_root):
            if workspace_root:
                try:
                    rel = Path(rc.workingDir).relative_to(workspace_root)
                    dir_suffix = f", dir: {rel}"
                except ValueError:
                    dir_suffix = f", dir: {rc.workingDir}"
            else:
                dir_suffix = f", dir: {rc.workingDir}"
        print(f"  cmd: `{rc.command}`" + dir_suffix)
        if rc.prompt:
            print(f"  prompt: {rc.prompt}")
        if output:
            for out_line in output.rstrip("\n").splitlines():
                print(f"  {out_line}")
        print()


async def _load_check_context(
    working_dir: str,
    runner: str | None,
    config: str | None,
) -> tuple[CheckContext | None, int | None]:
    from sensors.time_util import utc_now

    sm = _get_state_manager(working_dir, config)
    await sm.log_query("check", runner)
    now = utc_now()
    state = await sm.read_state()

    sensors_config = None
    try:
        sensors_config = load_config_sync(working_dir, config)
        runner_configs = {rc.name: rc for rc in sensors_config.runners}
    except Exception:
        runner_configs = {}

    on_check_configs = [
        rc
        for rc in (sensors_config.runners if sensors_config else [])
        if rc.mode == "on_check" and rc.enabled and (runner is None or rc.name == runner)
    ]

    if runner:
        state.runners = {k: v for k, v in state.runners.items() if k == runner}

    if not state.runners and not on_check_configs:
        print("No runner state found. Is the sensors running?", file=sys.stderr)
        return None, 2

    return (
        CheckContext(
            state=state,
            sensors_config=sensors_config,
            runner_configs=runner_configs,
            on_check_configs=on_check_configs,
            now=now,
        ),
        None,
    )


async def _run_check(working_dir: str, runner: str | None, config: str | None) -> int:
    ctx, early_exit = await _load_check_context(working_dir, runner, config)
    if early_exit is not None:
        return early_exit
    assert ctx is not None  # noqa: S101

    sm = _get_state_manager(working_dir, config)
    history_entry = HistoryEntry(
        timestamp=ctx.now,
        runner_filter=runner,
        runners={
            name: HistoryRunnerEntry(
                status=rs.status,
                score=(
                    {
                        "value": rs.reading.score.value,
                        "direction": rs.reading.score.direction,
                    }
                    if rs.reading
                    else None
                ),
                findings=rs.reading.findings if rs.reading else [],
            )
            for name, rs in ctx.state.runners.items()
        },
    )
    await sm.append_check_history(history_entry)

    workspace_root = str(Path(working_dir).resolve())
    display_configs = _relativize_runner_configs(ctx.runner_configs, workspace_root)
    _print_check_header(ctx.state, ctx.sensors_config, ctx.now)
    for name, rs in ctx.state.runners.items():
        _print_runner_result(name, rs, display_configs, ctx.state, ctx.now)
    await _print_on_check_sections(ctx.on_check_configs, workspace_root)
    return _check_exit_code(ctx.state)


def _get_state_manager(working_dir: str, config: str | None = None) -> StateManager:
    key = (working_dir, config)
    if key not in _state_managers:
        config_path = resolve_config_path(working_dir, config)
        state_file = state_path_for_config(config_path)
        history_file = history_path_for_config(config_path)
        _state_managers[key] = StateManager(state_file, history_file)
    return _state_managers[key]


def _wait_for_control_file(control_path: Path, timeout_sec: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if control_path.exists():
            return True
        time.sleep(0.05)
    return False


def _spawn_background_worker(working_dir: str, config: str | None) -> None:
    cmd = [sys.executable, "-m", "sensors.cli", "start", "--worker", str(Path(working_dir).resolve())]
    if config:
        cmd.extend(["--config", config])
    subprocess.Popen(  # noqa: S603 -- args are sys.executable + literals + user-provided working_dir; validated by caller
        cmd,
        cwd=str(Path(working_dir).resolve()),
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.command("start")
def start_command(
    working_dir: str = typer.Argument(..., help="Project working directory (contains .sensors/)"),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file name within .sensors/ (auto-detected if only one exists)",
    ),
    show: bool = typer.Option(
        False,
        "--show",
        help="Run in the foreground with the live overview display",
    ),
    worker: bool = typer.Option(
        False,
        "--worker",
        hidden=True,
        help="Internal: run sensors worker (headless, used by background spawn)",
    ),
) -> None:
    """Start the sensors. Without --show, starts in the background and exits immediately."""
    wd = str(Path(working_dir).resolve())
    if worker:
        try:
            asyncio.run(run_sensors(wd, config))
        except ConfigLoadError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(code=2) from None
        except RuntimeError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1) from None
        return
    try:
        cfg_path = resolve_config_path(wd, config)
    except ConfigLoadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2) from None

    ctl = control_path_for_config(cfg_path)
    sock = socket_path_for_config(cfg_path)
    from sensors.orchestration.control_server import ensure_no_live_sensors_sync

    try:
        ensure_no_live_sensors_sync(ctl, sock)
    except RuntimeError as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(code=1) from None

    _spawn_background_worker(wd, config)
    if not _wait_for_control_file(ctl):
        typer.echo(
            "Sensors subprocess started but control file was not created in time. "
            "Check stderr of the background process.",
            err=True,
        )
        raise typer.Exit(code=1)

    if not show:
        return

    # --show: attach a viewer to the freshly-started background process
    show_command(working_dir, config)


@app.command("show")
def show_command(
    working_dir: str = typer.Argument(..., help="Project working directory"),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file name within .sensors/",
    ),
) -> None:
    """Attach to a running sensors and show the overview (read-only viewer; Q does not stop the server)."""
    wd = str(Path(working_dir).resolve())
    _cfg_path, socket_path, _data = _require_running_sensors(
        wd, config, not_running_message=_NOT_RUNNING_SHOW
    )

    try:
        sensors_config = load_config_sync(wd, config)
    except ConfigLoadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2) from None

    sm = _get_state_manager(wd, config)
    display_box: list[DisplayManager | None] = [None]
    on_snapshot, on_clear, on_rerun, on_shutdown = _make_attach_callbacks(socket_path, display_box)

    events = DisplayEvents()
    display = DisplayManager(
        sm,
        runner_configs=sensors_config.runners,
        update_interval=1.0,
        events=events,
        attach=True,
        on_snapshot=on_snapshot,
        on_clear=on_clear,
        on_rerun=on_rerun,
        on_shutdown=on_shutdown,
    )
    display_box[0] = display

    asyncio.run(display.run())


@app.command("snapshot")
def snapshot_command(
    working_dir: str = typer.Argument(..., help="Project working directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file name within .sensors/"),
) -> None:
    """Tell the running sensors to save a score snapshot (same as pressing S in the TUI)."""
    wd = str(Path(working_dir).resolve())
    _cfg_path, socket_path, _data = _require_running_sensors(
        wd, config, not_running_message=_NOT_RUNNING_SNAPSHOT
    )

    res = rpc_unix_sync(socket_path, "snapshot", timeout=10.0)
    if res.get("ok"):
        typer.echo("SnapshotEntry saved.")
        raise typer.Exit(0)
    err = res.get("error", res)
    typer.echo(f"SnapshotEntry failed: {err}", err=True)
    raise typer.Exit(1)


def _status_all() -> None:
    """Print every OS process that looks like ``sensors start --worker`` or ``--show``."""
    from sensors.orchestration.sensors_processes import (
        compact_temp_path,
        display_project_path,
        list_sensors_start_processes,
        looks_like_macos_temp_sensors_dir,
    )

    if sys.platform == "win32":
        typer.echo("Listing all sensors processes is not supported on Windows.", err=True)
        raise typer.Exit(2)

    rows = list_sensors_start_processes()
    if not rows:
        typer.echo("No sensors start processes found (worker or --show).")
        return
    typer.echo("Sensors - start processes:")
    typer.echo(f"  {'pid':>6}  {'mode':<6}  project")
    any_temp = False
    for r in rows:
        raw = r.folder or "?"
        if raw == "?":
            line = "?"
        elif looks_like_macos_temp_sensors_dir(raw):
            any_temp = True
            line = compact_temp_path(raw) or display_project_path(raw)
        else:
            line = display_project_path(raw)
        typer.echo(f"  {r.pid:>6}  {r.mode:<6}  {line}")
    if any_temp:
        typer.echo(
            "\n(temp) paths are disposable dirs (e.g. sensors pytest e2e). "
            "Stop with: sensors stop --pid <pid>",
            err=True,
        )


@app.command("status")
def status_command(
    working_dir: str | None = typer.Argument(
        None,
        help="Project working directory (required unless --all)",
    ),
    all_projects: bool = typer.Option(
        False,
        "--all",
        "-a",
        help="List all sensors start processes on this machine (via /proc on Linux, else ps)",
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file name within .sensors/"),
) -> None:
    """Report whether a sensors is running for this project, or list all sensors start processes."""
    if all_projects:
        if working_dir is not None:
            typer.echo("Error: omit PROJECT when using --all.", err=True)
            raise typer.Exit(2)
        if config is not None:
            typer.echo("Error: --config cannot be used with --all.", err=True)
            raise typer.Exit(2)
        _status_all()
        raise typer.Exit(0)

    if working_dir is None:
        typer.echo("Error: PROJECT is required (or use --all).", err=True)
        raise typer.Exit(2)

    wd = str(Path(working_dir).resolve())
    try:
        cfg_path = resolve_config_path(wd, config)
    except ConfigLoadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(code=2) from None

    ctl = control_path_for_config(cfg_path)
    sock = socket_path_for_config(cfg_path)
    if is_sensors_running(ctl, sock):
        data = control_state(ctl)
        pid = data.get("pid", "?") if data else "?"
        typer.echo(f"Sensors is running (pid {pid}).")
        raise typer.Exit(0)
    typer.echo("Sensors is not running.", err=True)
    raise typer.Exit(1)


@app.command("check")
def check_command(
    working_dir: str = typer.Argument(..., help="Project working directory"),
    runner: str | None = typer.Option(None, "--runner", "-r", help="Show only this runner"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file name within .sensors/"),
) -> None:
    """Print runner results (agent-friendly text). Exit 0 if all pass, 1 if any failure, 2 if no state, 3 if any below threshold."""
    raise typer.Exit(asyncio.run(_run_check(working_dir, runner, config)))


async def _run_on_check_command(runner_cfg: RunnerConfig) -> str:
    """Execute an on_check runner's command and return its combined stdout/stderr output."""
    import os as _os

    env = {**_os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    try:
        process = await asyncio.create_subprocess_shell(
            runner_cfg.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=runner_cfg.workingDir,
            env=env,
        )
        timeout_sec = runner_cfg.commandTimeout
        if timeout_sec is not None and timeout_sec > 0:
            try:
                stdout, _ = await asyncio.wait_for(
                    process.communicate(), timeout=float(timeout_sec)
                )
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return f"[sensors] command timed out after {timeout_sec}s"
        else:
            stdout, _ = await process.communicate()
        return stdout.decode("utf-8", errors="ignore")
    except Exception as e:  # noqa: BLE001 -- surface error inline so agents can see it
        return f"[sensors] failed to execute command: {e}"


def _stop_by_pid(pid: int, wait: bool, timeout_sec: float) -> None:
    """Send SIGTERM to a sensors process by PID after verifying it is one."""
    import contextlib
    import os
    import signal

    from sensors.orchestration.sensors_processes import list_sensors_start_pids

    known_pids = list_sensors_start_pids()
    if pid not in known_pids:
        typer.echo(
            f"PID {pid} is not a known sensors process. "
            f"Use `sensors status --all` to list sensors processes.",
            err=True,
        )
        raise typer.Exit(1)

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        typer.echo(f"No process with pid {pid}.", err=True)
        raise typer.Exit(1) from None
    except PermissionError:
        typer.echo(f"Permission denied for pid {pid}.", err=True)
        raise typer.Exit(1) from None

    os.kill(pid, signal.SIGTERM)
    if not wait:
        typer.echo(f"Sent SIGTERM to pid {pid}.")
        raise typer.Exit(0)

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            typer.echo(f"Stopped sensors (pid {pid}).")
            raise typer.Exit(0) from None
        time.sleep(0.1)

    # SIGTERM didn't work (e.g. stuck I/O in orphaned worker) -- escalate to SIGKILL.
    typer.echo(f"PID {pid} did not exit after SIGTERM; sending SIGKILL.", err=True)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    for _ in range(20):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            typer.echo(f"Killed sensors (pid {pid}).")
            raise typer.Exit(0) from None
        time.sleep(0.1)
    typer.echo(f"PID {pid} did not exit even after SIGKILL.", err=True)
    raise typer.Exit(1)


@app.command("stop")
def stop_command(
    working_dir: str | None = typer.Argument(
        None,
        help="Project working directory (required unless --pid is given)",
    ),
    pid: int | None = typer.Option(
        None,
        "--pid",
        "-p",
        help="Stop a sensors process by PID (from `sensors status --all`)",
    ),
    config: str | None = typer.Option(
        None,
        "--config",
        "-c",
        help="Config file name within .sensors/ (same as sensors start)",
    ),
    wait: bool = typer.Option(
        True,
        "--wait/--no-wait",
        help="Wait until the process exits (after SIGTERM)",
    ),
    timeout_sec: float = typer.Option(
        15.0,
        "--timeout",
        min=0.5,
        help="Max seconds to wait for exit after SIGTERM (when --wait)",
    ),
) -> None:
    """Stop a background sensors by project directory or PID."""
    if pid is not None:
        if working_dir is not None:
            typer.echo("Error: provide either PROJECT or --pid, not both.", err=True)
            raise typer.Exit(2)
        _stop_by_pid(pid, wait, timeout_sec)
        return

    if working_dir is None:
        typer.echo("Error: PROJECT is required (or use --pid <pid>).", err=True)
        raise typer.Exit(2)

    wd = str(Path(working_dir).resolve())
    try:
        cfg_path = resolve_config_path(wd, config)
    except ConfigLoadError as e:
        typer.echo(f"Error: {e}", err=True)
        raise typer.Exit(2) from None

    ctl = control_path_for_config(cfg_path)
    sock = socket_path_for_config(cfg_path)

    msg, code = stop_running_sensors(
        ctl,
        sock,
        wait=wait,
        timeout_sec=timeout_sec,
    )
    typer.echo(msg)
    raise typer.Exit(code)


@app.command("refresh")
def refresh_command(
    working_dir: str = typer.Argument(..., help="Project working directory"),
    runner: str | None = typer.Option(None, "--runner", "-r", help="Refresh only this runner"),
    config: str | None = typer.Option(None, "--config", "-c", help="Config file name within .sensors/"),
) -> None:
    """Trigger immediate refresh (not yet implemented)."""
    print("Refresh is not yet implemented. Run the sensors to update state.", file=sys.stderr)
    sys.exit(0)


def main() -> None:
    """Entry point for the sensors CLI."""
    app()


if __name__ == "__main__":
    main()
