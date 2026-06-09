"""Vitest output parser."""

import re
from datetime import datetime
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser


class VitestParser(OutputParser):
    """Parser for Vitest test output.

    Supports both watch mode (detects test completion) and interval mode.
    In watch mode, delegates all file-watching and rerun logic to vitest.
    Detects completed runs by looking for the "Tests  X passed/failed" summary line.
    """

    def is_watch_run_complete(self, line: str) -> bool:
        """Detect vitest test summary line indicating a run has completed.

        Matches the actual summary line which always has a number before passed/failed:
          "      Tests  6 passed (6)"
          "      Tests  1 failed | 5 passed (6)"
        Does NOT match status messages like "FAIL  Tests failed. Watching for file changes..."
        """
        return bool(re.search(r'Tests\s+\d+\s+(failed|passed)', line))

    async def parse_output(self, output: str) -> RunnerResult:
        """Parse Vitest text output into RunnerResult.

        Handles all vitest summary formats:
          "Tests  1 failed | 5 passed (6)"  — mixed
          "Tests  6 passed (6)"             — all pass
          "Tests  1 failed (1)"             — all fail (watch mode partial rerun)
        """
        text = output.strip()

        num_passed = 0
        num_failed = 0

        # Match the "Tests" summary line (not "Test Files")
        tests_line = re.search(r'^\s*Tests\s+(.+)$', text, re.MULTILINE)
        if tests_line:
            line = tests_line.group(1)
            fm = re.search(r'(\d+)\s+failed', line)
            if fm:
                num_failed = int(fm.group(1))
            pm = re.search(r'(\d+)\s+passed', line)
            if pm:
                num_passed = int(pm.group(1))

        # Also check "Test Files" for suite-level failures (e.g. import errors where
        # individual tests never ran so Tests line shows 0 failed)
        if num_failed == 0:
            files_line = re.search(r'^\s*Test Files\s+(.+)$', text, re.MULTILINE)
            if files_line:
                ffm = re.search(r'(\d+)\s+failed', files_line.group(1))
                if ffm:
                    num_failed = int(ffm.group(1))

        success = num_failed == 0
        failures = self._extract_failures(text)

        out: dict[str, Any] = {
            "numPassedTests": num_passed,
            "numFailedTests": num_failed,
            "failures": failures,
        }

        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output=out,
        )

    def _extract_failures(self, text: str) -> list[dict[str, str]]:
        """Extract structured failure information from vitest output.

        Handles two vitest failure section formats:

        Test-level failures (assertion errors):
          ⎯⎯⎯ Failed Tests 1 ⎯⎯⎯
           FAIL  src/file.test.ts > Suite > test name
          AssertionError: ...
          ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯

        Suite-level failures (e.g. import errors):
          ⎯⎯⎯ Failed Suites 2 ⎯⎯⎯
           FAIL   project  src/file.test.ts [ src/file.test.ts ]
          Error: Cannot find module ...
          ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/2]⎯
        """
        failures: list[dict[str, str]] = []

        # Match either "Failed Tests" or "Failed Suites" section header.
        # Greedy (.*) so we capture up to the LAST [N/N] footer (not the first separator
        # between items).
        section_match = re.search(
            r'Failed (?:Tests|Suites) \d+.*?\n(.*)\[\d+/\d+\]',
            text, re.DOTALL,
        )
        if not section_match:
            return failures

        section = section_match.group(1)

        # Split on FAIL lines to get individual failure blocks
        parts = re.split(r'\bFAIL\s{2,}', section)

        for part in parts[1:]:  # Skip content before first FAIL
            lines = part.strip().split('\n', 1)
            raw_header = lines[0].strip()
            message = lines[1].strip() if len(lines) > 1 else ""

            # Suite failure format: "project  path/to/file.test.ts [ /abs/path/to/file.test.ts ]"
            # Strip the absolute-path bracket trailer, then strip optional project prefix.
            test_path = re.sub(r'\s*\[.*?\]\s*$', '', raw_header).strip()
            project_prefix = re.match(r'^\w[\w-]*\s{2,}(\S.*)', test_path)
            if project_prefix:
                test_path = project_prefix.group(1).strip()

            failures.append({
                "type": "test_failure",
                "test": test_path,
                "message": message[:500],
            })

        return failures

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        _, failed = self._counts(result)
        return ScoreInfo(value=failed, direction="less", description="Number of failing tests")

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> tuple:
        output = result.output
        return output.get("numPassedTests", 0), output.get("numFailedTests", 0)

    def _summary_text(self, passed: int, failed: int) -> str:
        if failed == 0:
            return f"{passed} passed"
        return f"{failed} failed, {passed} passed"

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        passed, failed = self._counts(result)
        text = self._summary_text(passed, failed)
        if failed > 0:
            return f"[red]{text}[/red]"
        return f"[green]{text}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        passed, failed = self._counts(result)
        text = self._summary_text(passed, failed)
        if failed > 0:
            return f'<span class="sensors-error">{text}</span>'
        return f'<span class="sensors-success">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        passed, failed = self._counts(result)
        return self._summary_text(passed, failed)

    # -- Failures (multi-line) --

    def _format_failure_items(self, result: RunnerResult, style: str) -> str:
        """Shared logic for formatting failure items across output styles."""
        if result.success:
            return ""

        failures = result.output.get("failures", [])

        if not failures:
            _, failed = self._counts(result)
            msg = f"{failed} test{'s' if failed != 1 else ''} failed (no details available)"
            if style == "terminal":
                return f"  [red]{msg}[/red]"
            if style == "html":
                return f'<span class="sensors-error">{msg}</span>'
            return f"  {msg}"

        lines = []
        for failure in failures:
            test_breadcrumb = failure.get("test", "unknown")
            message = failure.get("message", "")[:200]
            # Vitest breadcrumb: "file.test.ts > Suite > test name"
            parts = [p.strip() for p in test_breadcrumb.split(" > ")]
            file_part = parts[0] if parts else "unknown"
            test_part = " > ".join(parts[1:]) if len(parts) > 1 else test_breadcrumb
            if style == "terminal":
                lines.append(f"  [red]FAIL:[/red] [dim]{file_part}[/dim] > {test_part}")
                if message:
                    lines.append(f"    {message}")
            elif style == "html":
                lines.append(
                    f'<div class="sensors-violation">'
                    f'<span class="sensors-error">FAIL:</span> '
                    f'<span class="sensors-file">{file_part}</span>'
                    f' &rsaquo; {test_part}'
                    f'<div class="sensors-message">{message}</div>'
                    f'</div>'
                )
            else:
                lines.append(f"  FAIL: {file_part} > {test_part}")
                if message:
                    lines.append(f"    {message}")

        return "\n".join(lines)

    def format_failures_terminal(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "terminal")

    def format_failures_html(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "html")

    def format_failures_llm(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "llm")
