"""Pytest output parser."""

import re
from typing import Any

from sensors.config import Finding, Metric, ScoreInfo, SensorReading

from .base import OutputParser


class PytestParser(OutputParser):
    """Parser for pytest text output."""

    def parse(self, output: str) -> SensorReading:
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

        warning_matches = re.findall(r'warnings label', text, re.IGNORECASE)
        if warning_matches:
            warning_section = re.search(r'warnings label.*?(?=\n\n|\n=|$)', text, re.DOTALL | re.IGNORECASE)
            if warning_section:
                warning_lines = [line for line in warning_section.group(0).split('\n')
                               if line.strip() and not line.strip().startswith('warnings label')
                               and not line.strip().startswith('--')]
                warning_files = set()
                for line in warning_lines:
                    if re.match(r'^[^\s].*\.py:\d+', line.strip()):
                        warning_files.add(line.strip().split(':')[0])
                num_warnings = len(warning_files)

        failures = self._extract_failures(text)
        success = num_failed == 0 and num_errors == 0
        findings = [
            self._finding_from_failure(failure)
            for failure in failures
        ]

        return SensorReading(
            success=success,
            summary=self._summary_text(num_passed, num_failed, num_errors),
            score=ScoreInfo(
                value=num_failed + num_errors,
                direction="less",
                description="Number of failing tests and errors",
            ),
            findings=[f for f in findings if f is not None],
            metrics=[
                Metric("passed", "Passed", num_passed, direction="more"),
                Metric("failed", "Failed", num_failed),
                Metric("errors", "Errors", num_errors),
                Metric("warnings", "Warnings", num_warnings),
            ],
            extra={"label": self._extract_summary(text)},
        )

    def _finding_from_failure(self, failure: dict[str, Any]) -> Finding | None:
        failure_type = failure.get("type")
        if failure_type == "collection_error":
            return Finding(
                file=failure.get("file"),
                rule="collection_error",
                message=failure.get("message", ""),
                severity="error",
            )
        if failure_type == "test_failure":
            return Finding(
                file=failure.get("test"),
                rule="test_failure",
                message=failure.get("message", ""),
                severity="error",
            )
        return None

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

