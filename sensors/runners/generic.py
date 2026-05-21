"""Generic runner that can execute any command with pluggable output parsers.

This module provides a single runner implementation that handles all process management
(spawning, watching, intervals) and delegates parsing to pluggable OutputParser instances.
"""

import asyncio
import contextlib
import logging
import os
import platform
import re
import shlex
from datetime import datetime
from pathlib import Path

from sensors.config.schema import RunnerConfig, RunnerMode
from sensors.persistence.models import FormattedOutput, RunnerResult, RunnerState, ScoreInfo
from sensors.persistence.state_manager import StateManager
from sensors.runners.parsers.base import OutputParser

logger = logging.getLogger(__name__)

# When commandTimeout is omitted (None), wait this long before killing the subprocess.
# Previously a hardcoded 300s caused long jobs (e.g. Stryker) to never persist state.
_DEFAULT_INTERVAL_COMMAND_TIMEOUT_SEC = 7200.0

# Matches ANSI escape codes, carriage returns, and OSC sequences
_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\].*?(?:\x07|\x1b\\)|\r')


class GenericRunner:
    """Generic runner that can execute any command with a pluggable output parser.

    This runner handles all process management:
    - Watch mode: Long-running processes with streaming output
    - Interval mode: Periodic command execution (with optional early re-run via TUI digit keys)
    - Triggered mode: Runs only when signaled from the TUI (digit key)
    - PTY support via Unix `script` command for watch-mode tools
    - Automatic restart with exponential backoff

    All parsing and formatting is delegated to the injected OutputParser plugin.

    Attributes:
        config: Runner configuration (command, mode, interval, etc.)
        parser: Output parser plugin for tool-specific parsing
        state_manager: State manager for persisting results
    """

    def __init__(
        self,
        config: RunnerConfig,
        parser: OutputParser,
        state_manager: StateManager | None = None,
        *,
        rerun_event: asyncio.Event | None = None,
    ):
        """Initialize the generic runner.

        Args:
            config: Runner configuration
            parser: Output parser plugin
            state_manager: Optional state manager for persisting results
            rerun_event: Set by the TUI to re-run interval/triggered runners early; required for
                those modes when running under the sensors orchestrator.
        """
        self.config = config
        self.parser = parser
        self.state_manager = state_manager
        self.rerun_event = rerun_event
        self._task: asyncio.Task | None = None
        self._should_stop = False

    async def start(self) -> None:
        """Start the runner in the configured mode.

        For watch mode: Spawns a long-running process and parses streaming output
        For interval mode: Schedules periodic command execution
        For triggered mode: Runs only when ``rerun_event`` is signaled
        """
        mode = self.config.mode
        if mode == RunnerMode.WATCH:
            await self._run_watch_mode()
        elif mode == RunnerMode.TRIGGERED:
            if self.rerun_event is None:
                raise ValueError(
                    f"Runner {self.config.name!r} is triggered mode but rerun_event was not set"
                )
            await self._run_triggered_mode()
        else:
            if self.rerun_event is None:
                raise ValueError(
                    f"Runner {self.config.name!r} is interval mode but rerun_event was not set"
                )
            await self._run_interval_mode()

    async def stop(self) -> None:
        """Stop the runner gracefully."""
        self._should_stop = True
        if self.rerun_event is not None:
            self.rerun_event.set()

    async def on_result(self, result: RunnerResult) -> None:
        """Handle a new runner result by persisting to state manager.

        Computes all 6 formatted strings via the parser before persisting.

        Args:
            result: The parsed runner result
        """
        if not self.state_manager:
            return
        if result.output.get("sensorsCommandTimeout"):
            limit = result.output.get("sensorsTimeoutSeconds")
            msg = (
                f"Command timed out after {limit}s"
                if limit is not None
                else "Command timed out"
            )
            formatted = FormattedOutput(
                details_terminal=f"[red]{msg}[/red]",
                details_html=f'<span class="sensors-error">{msg}</span>',
                details_llm=msg,
                failures_terminal=f"  [red]{msg}[/red]",
                failures_html=f'<span class="sensors-error">{msg}</span>',
                failures_llm=msg,
            )
            score = ScoreInfo(
                value=0,
                direction="more",
                description="Last run did not finish (timeout)",
            )
            status = "failure"
        else:
            status = "success" if result.success else "failure"
            formatted = FormattedOutput(
                details_terminal=self.parser.format_details_terminal(result),
                details_html=self.parser.format_details_html(result),
                details_llm=self.parser.format_details_llm(result),
                failures_terminal=self.parser.format_failures_terminal(result),
                failures_html=self.parser.format_failures_html(result),
                failures_llm=self.parser.format_failures_llm(result),
            )
            score = self.parser.calculate_score(result)
            threshold = self.config.threshold
            if status == "success" and score is not None and threshold is not None:
                below = (
                    score.value < threshold
                    if score.direction == "more"
                    else score.value > threshold
                )
                if below:
                    status = "below_threshold"
                    note = f"below target threshold of {threshold:g}"
                    formatted = FormattedOutput(
                        details_terminal=f"{formatted.details_terminal} ({note})",
                        details_html=f"{formatted.details_html} ({note})",
                        details_llm=f"{formatted.details_llm} ({note})",
                        failures_terminal=formatted.failures_terminal,
                        failures_html=formatted.failures_html,
                        failures_llm=formatted.failures_llm,
                    )
        if self.config.mode == RunnerMode.WATCH:
            mode_label = "watch"
        elif self.config.mode == RunnerMode.TRIGGERED:
            mode_label = "triggered"
        elif self.config.interval:
            secs = self.config.interval / 1000
            mode_label = f"every {secs:g}s"
        else:
            mode_label = "interval"
        runner_state = RunnerState(
            lastRun=result.timestamp,
            status=status,
            mode=mode_label,
            formatted=formatted,
            score=score,
        )
        await self.state_manager.update_state(self.config.name, runner_state)

    @staticmethod
    def strip_ansi(text: str) -> str:
        """Strip ANSI escape codes, OSC sequences, and carriage returns from text."""
        return _ANSI_ESCAPE_RE.sub('', text)

    async def _parser_input_from_run(self, stdout_stripped: str) -> str | RunnerResult:
        """Text to pass to ``parse_output``: stdout, or contents of ``config.result`` when set.

        When ``result`` is set, the command is expected to write that file; after the run
        completes we read it and ignore stdout for parsing (stdout is still kept for error context).

        Retries on FileNotFoundError to handle the brief window where the tool deletes the
        previous file before writing the new one (e.g. vitest recreating coverage-final.json).
        """
        if not self.config.result:
            return stdout_stripped
        path = Path(self.config.result)

        def _read() -> str:
            return path.read_text(encoding="utf-8", errors="replace")

        last_exc: OSError | None = None
        for _ in range(4):
            try:
                text = await asyncio.to_thread(_read)
                return self.strip_ansi(text)
            except FileNotFoundError as e:
                last_exc = e
                await asyncio.sleep(0.25)
            except OSError as e:
                last_exc = e
                break

        return RunnerResult(
            timestamp=datetime.now(),
            success=False,
            output={
                "parseError": f"Could not read result file {path}: {last_exc}",
                "raw": stdout_stripped[:500],
            },
        )

    @staticmethod
    def _wrap_with_script(command: str) -> str:
        """Wrap a command with ``script`` to provide a pseudo-TTY.

        Many watch-mode tools (vitest, jest, tsc, cargo watch, etc.) require an
        interactive terminal to stay in watch mode. The ``script`` Unix command
        provides a reliable PTY wrapper without any Python-level PTY management.

        Supports both Linux (util-linux) and macOS (BSD) variants::

            Linux: script -qfc "command" /dev/null
            macOS: script -q /dev/null command
        """
        if platform.system() == 'Darwin':
            # macOS BSD script: script -q file command [args...]
            return f'script -q /dev/null {command}'
        else:
            # Linux util-linux script: script -qfc "command" file
            # -f flushes output after each write (real-time streaming)
            return f'script -qfc {shlex.quote(command)} /dev/null'

    async def _run_watch_mode(self) -> None:
        """Execute runner in watch mode using ``script`` for TTY support.

        Uses the ``script`` Unix command to provide a pseudo-TTY so the watched
        process believes it's running in an interactive terminal. Output is read
        line-by-line, stripped of ANSI codes, accumulated, and parsed when
        the parser's ``is_watch_run_complete()`` detects a run boundary.

        All file-watching and rerun-triggering logic is delegated to the watched
        process (e.g. vitest decides which file changes should trigger a rerun).
        The sensors only detects completed runs via stdout inspection.

        Includes automatic restart with exponential backoff on process crashes.
        """
        retry_delay = 1.0
        max_retry_delay = 60.0

        while not self._should_stop:
            process = None
            try:
                command = self.config.watchCommand or self.config.command
                wrapped_command = self._wrap_with_script(command)

                # Disable color output so we get clean text to parse
                env = {**os.environ, 'NO_COLOR': '1', 'FORCE_COLOR': '0'}

                process = await asyncio.create_subprocess_shell(
                    wrapped_command,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,  # merge stderr into stdout
                    stdin=asyncio.subprocess.DEVNULL,
                    cwd=self.config.workingDir,
                    env=env,
                )

                retry_delay = 1.0
                accumulated_output: list[str] = []

                if process.stdout:
                    async for raw_line in process.stdout:
                        if self._should_stop:
                            break

                        line = self.strip_ansi(
                            raw_line.decode('utf-8', errors='ignore')
                        ).rstrip('\n')

                        accumulated_output.append(line)

                        if self.parser.is_watch_run_complete(line):
                            try:
                                complete_output = '\n'.join(accumulated_output)
                                stripped = self.strip_ansi(complete_output)
                                parse_input = await self._parser_input_from_run(stripped)
                                if isinstance(parse_input, RunnerResult):
                                    result = parse_input
                                else:
                                    result = await self.parser.parse_output(parse_input)
                                await self.on_result(result)
                            except Exception as e:
                                print(f"[{self.config.name}] Parse error: {e}")
                                logger.exception(
                                    "[%s] Parse error in watch mode", self.config.name
                                )
                            # Reset accumulator for next run
                            accumulated_output = []

                if process:
                    await process.wait()

                if not self._should_stop:
                    print(f"[{self.config.name}] Watch process exited, restarting in {retry_delay}s")
                    logger.warning(
                        "[%s] Watch process exited unexpectedly, restarting in %.1fs",
                        self.config.name,
                        retry_delay,
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)

            except Exception as e:
                if not self._should_stop:
                    print(f"[{self.config.name}] Error in watch mode: {e}")
                    logger.exception("[%s] Unhandled error in watch mode", self.config.name)
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, max_retry_delay)
            finally:
                # Ensure the process is terminated on exit
                if process and process.returncode is None:
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=2.0)
                    except asyncio.TimeoutError:
                        # Process didn't terminate gracefully, force kill
                        try:
                            process.kill()
                            await asyncio.wait_for(process.wait(), timeout=1.0)
                        except Exception:
                            logger.debug("Process cleanup failed during shutdown", exc_info=True)
                    except Exception:
                        logger.debug("Process already dead or cleanup error", exc_info=True)

    async def _run_interval_command_once(self) -> None:
        """Run the interval/triggered shell command once and persist the result."""
        try:
            env = {**os.environ, 'NO_COLOR': '1', 'FORCE_COLOR': '0'}

            process = await asyncio.create_subprocess_shell(
                self.config.command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self.config.workingDir,
                env=env,
            )

            timeout_sec = self.config.commandTimeout
            if timeout_sec is None:
                timeout_sec = _DEFAULT_INTERVAL_COMMAND_TIMEOUT_SEC
            use_wait_for = timeout_sec > 0

            try:
                if use_wait_for:
                    stdout, _ = await asyncio.wait_for(
                        process.communicate(),
                        timeout=float(timeout_sec),
                    )
                else:
                    stdout, _ = await process.communicate()

                output = self.strip_ansi(stdout.decode('utf-8', errors='ignore'))
                parse_input = await self._parser_input_from_run(output)
                if isinstance(parse_input, RunnerResult):
                    result = parse_input
                else:
                    result = await self.parser.parse_output(parse_input)
                await self.on_result(result)

            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                print(
                    f"[{self.config.name}] Command timed out after {int(timeout_sec)}s"
                )
                logger.error(
                    "[%s] Command timed out after %ss",
                    self.config.name,
                    int(timeout_sec),
                )
                await self.on_result(
                    RunnerResult(
                        timestamp=datetime.now(),
                        success=False,
                        output={
                            "sensorsCommandTimeout": True,
                            "sensorsTimeoutSeconds": int(timeout_sec),
                        },
                    )
                )

        except Exception as e:
            print(f"[{self.config.name}] Error in interval mode: {e}")
            logger.exception("[%s] Unhandled error in interval mode", self.config.name)

    async def _wait_interval_or_rerun(self, interval_seconds: float) -> None:
        """Sleep for ``interval_seconds`` or until ``rerun_event`` is set (early re-run)."""
        if self._should_stop or interval_seconds <= 0:
            return
        ev = self.rerun_event
        if ev is None:
            await asyncio.sleep(interval_seconds)
            return

        sleep_task = asyncio.create_task(asyncio.sleep(interval_seconds))
        event_task = asyncio.create_task(ev.wait())
        done, pending = await asyncio.wait(
            {sleep_task, event_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t
        if ev.is_set():
            ev.clear()

    async def _run_triggered_mode(self) -> None:
        """Run only when ``rerun_event`` is signaled (keyboard shortcut in the TUI)."""
        ev = self.rerun_event
        if ev is None:
            raise ValueError(
                f"Runner {self.config.name!r} is triggered mode but rerun_event was not set"
            )
        while not self._should_stop:
            await ev.wait()
            if self._should_stop:
                break
            ev.clear()
            await self._run_interval_command_once()

    async def _run_interval_mode(self) -> None:
        """Execute runner in interval mode with periodic scheduling.

        Runs the command to completion, parses output, waits for the configured
        interval (or a re-run signal), and repeats until stopped.
        """
        if self.config.interval is None:
            raise ValueError(
                f"Runner {self.config.name} is in interval mode but has no interval configured"
            )

        interval_seconds = self.config.interval / 1000.0  # Convert ms to seconds

        while not self._should_stop:
            await self._run_interval_command_once()
            if not self._should_stop:
                await self._wait_interval_or_rerun(interval_seconds)

    @property
    def is_watch_mode(self) -> bool:
        """Check if this runner is configured for watch mode."""
        return self.config.mode == RunnerMode.WATCH

    @property
    def is_interval_mode(self) -> bool:
        """Check if this runner is configured for interval mode."""
        return self.config.mode == RunnerMode.INTERVAL
