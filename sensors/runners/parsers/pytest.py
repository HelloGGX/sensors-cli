"""Pytest output parser."""

import re
from datetime import datetime
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser


class PytestParser(OutputParser):
    """Parser for pytest text output."""

    async def parse_output(self, output: str) -> RunnerResult:
        text = output.strip()

        num_passed = 0
        num_failed = 0
        num_errors = 0
        num_warnings = 0

        passed_match = re.search(r'(\d+)\s+passed', text)
        if passed_match:
            num_passed = int(passed_match.group(1))

        failed_match = re.search(r'(\d+)\s+failed', text)
        if failed_match:
            num_failed = int(failed_match.group(1))

        error_matches = re.findall(r'ERROR collecting', text)
        num_errors = len(error_matches)

        warning_matches = re.findall(r'warnings summary', text, re.IGNORECASE)
        if warning_matches:
            warning_section = re.search(r'warnings summary.*?(?=\n\n|\n=|$)', text, re.DOTALL | re.IGNORECASE)
            if warning_section:
                warning_lines = [line for line in warning_section.group(0).split('\n')
                               if line.strip() and not line.strip().startswith('warnings summary')
                               and not line.strip().startswith('--')]
                warning_files = set()
                for line in warning_lines:
                    if re.match(r'^[^\s].*\.py:\d+', line.strip()):
                        warning_files.add(line.strip().split(':')[0])
                num_warnings = len(warning_files)

        failures = self._extract_failures(text)
        success = num_failed == 0 and num_errors == 0

        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output={
                "numPassedTests": num_passed,
                "numFailedTests": num_failed,
                "numErrors": num_errors,
                "numWarnings": num_warnings,
                "failures": failures,
                "summary": self._extract_summary(text)
            }
        )

    def _extract_failures(self, text: str) -> list[dict[str, Any]]:
        failures = []

        error_pattern = r'ERROR collecting ([^\n]+)\n(.*?)(?=\n=|$)'
        for match in re.finditer(error_pattern, text, re.DOTALL):
            test_file = match.group(1).strip()
            error_details = match.group(2).strip()
            failures.append({
                "type": "collection_error",
                "file": test_file,
                "message": error_details[:500]
            })

        failure_section = re.search(r'FAILURES.*?(?=\n=|$)', text, re.DOTALL)
        if failure_section:
            test_failures = re.findall(r'_+ (.+?) _+\n(.*?)(?=\n_+|$)', failure_section.group(0), re.DOTALL)
            for test_name, failure_details in test_failures:
                failures.append({
                    "type": "test_failure",
                    "test": test_name.strip(),
                    "message": failure_details.strip()[:500]
                })

        return failures

    def _extract_summary(self, text: str) -> str:
        summary_match = re.search(r'=+ (.+?) =+$', text.strip(), re.MULTILINE)
        if summary_match:
            return summary_match.group(1).strip()
        lines = text.strip().split('\n')
        return ' '.join(lines[-3:]) if len(lines) >= 3 else text[-200:]

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        _, failed, errors = self._counts(result)
        return ScoreInfo(value=failed + errors, direction="less", description="Number of failing tests and errors")

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> tuple:
        output = result.output
        return (
            output.get("numPassedTests", 0),
            output.get("numFailedTests", 0),
            output.get("numErrors", 0),
        )

    def _summary_text(self, passed: int, failed: int, errors: int) -> str:
        if failed == 0 and errors == 0:
            return f"{passed} passed"
        parts = []
        if errors > 0:
            parts.append(f"{errors} error{'s' if errors != 1 else ''}")
        if failed > 0:
            parts.append(f"{failed} failed")
        if passed > 0:
            parts.append(f"{passed} passed")
        return ", ".join(parts)

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        passed, failed, errors = self._counts(result)
        text = self._summary_text(passed, failed, errors)
        if failed > 0 or errors > 0:
            return f"[red]{text}[/red]"
        return f"[green]{text}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        passed, failed, errors = self._counts(result)
        text = self._summary_text(passed, failed, errors)
        if failed > 0 or errors > 0:
            return f'<span class="sensors-error">{text}</span>'
        return f'<span class="sensors-success">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        passed, failed, errors = self._counts(result)
        return self._summary_text(passed, failed, errors)

    # -- Failures (multi-line) --

    def _format_failure_items(self, result: RunnerResult, style: str) -> str:
        """Shared logic for formatting failure items.

        Args:
            style: "terminal", "html", or "llm"
        """
        if result.success:
            return ""

        failures = result.output.get("failures", [])
        if not failures:
            _, failed, errors = self._counts(result)
            return _format_no_failure_details(failed + errors, style)

        lines: list[str] = []
        for failure in failures:
            lines.extend(_format_single_failure(failure, style))
        return "\n".join(lines)

    def format_failures_terminal(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "terminal")

    def format_failures_html(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "html")

    def format_failures_llm(self, result: RunnerResult) -> str:
        return self._format_failure_items(result, "llm")


def _format_no_failure_details(total: int, style: str) -> str:
    msg = f"{total} issue{'s' if total != 1 else ''} (no details available)"
    if style == "terminal":
        return f"  [red]{msg}[/red]"
    if style == "html":
        return f'<span class="sensors-error">{msg}</span>'
    return f"  {msg}"


def _format_single_failure(failure: dict[str, Any], style: str) -> list[str]:
    if failure.get("type") == "collection_error":
        file_ref = failure.get("file", "unknown")
        message = failure.get("message", "")[:200]
        if style == "terminal":
            return [
                f"  [red]COLLECTION ERROR:[/red] [dim]{file_ref}[/dim]",
                f"    {message}",
            ]
        if style == "html":
            return [
                f'<div class="sensors-violation">'
                f'<span class="sensors-error">COLLECTION ERROR:</span> '
                f'<span class="sensors-file">{file_ref}</span>'
                f'<div class="sensors-message">{message}</div>'
                f'</div>'
            ]
        return [
            f"  COLLECTION ERROR: {file_ref}",
            f"    {message}",
        ]

    if failure.get("type") == "test_failure":
        test_name = failure.get("test", "unknown")
        message = failure.get("message", "")[:200]
        if style == "terminal":
            return [
                f"  [red]TEST FAILURE:[/red] {test_name}",
                f"    {message}",
            ]
        if style == "html":
            return [
                f'<div class="sensors-violation">'
                f'<span class="sensors-error">TEST FAILURE:</span> {test_name}'
                f'<div class="sensors-message">{message}</div>'
                f'</div>'
            ]
        return [
            f"  TEST FAILURE: {test_name}",
            f"    {message}",
        ]

    return []
