"""Stylelint output parser (--formatter json)."""

import json
from datetime import datetime
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser


class StylelintParser(OutputParser):
    """Parser for Stylelint JSON formatter output."""

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            data = json.loads(self._strip_preamble(output))
        except json.JSONDecodeError as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

        if not isinstance(data, list):
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={
                    "parseError": "Expected top-level JSON array from stylelint",
                    "raw": output[:500],
                },
            )

        violations = self._extract_violations(data)
        error_count = sum(1 for v in violations if v.get("severity", 2) == 2)
        warning_count = sum(1 for v in violations if v.get("severity", 2) == 1)
        success = error_count == 0 and warning_count == 0

        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output={
                "errorCount": error_count,
                "warningCount": warning_count,
                "violations": violations,
            },
        )

    def _extract_violations(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for f in files:
            path = f.get("source", "")
            for msg in f.get("warnings", []):
                sev_raw = str(msg.get("severity", "error")).lower()
                severity = 2 if sev_raw == "error" else 1
                violations.append(
                    {
                        "file": path,
                        "line": msg.get("line", 0),
                        "column": msg.get("column", 0),
                        "message": msg.get("text", ""),
                        "severity": severity,
                        "ruleId": msg.get("rule", ""),
                    }
                )
            for err in f.get("parseErrors", []):
                violations.append(
                    {
                        "file": path,
                        "line": err.get("line", 0),
                        "column": err.get("column", 0),
                        "message": err.get("text", "parse error"),
                        "severity": 2,
                        "ruleId": err.get("stylelintType", "parse-error"),
                    }
                )
        return violations

    @staticmethod
    def _strip_preamble(output: str) -> str:
        for i, ch in enumerate(output):
            if ch == "[":
                return output[i:]
        return output

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        ec, wc = self._counts(result)
        return ScoreInfo(value=ec + wc, direction="less", description="Number of stylelint issues")

    def _counts(self, result: RunnerResult) -> tuple[int, int]:
        output = result.output
        return output.get("errorCount", 0), output.get("warningCount", 0)

    def _summary_text(self, error_count: int, warning_count: int) -> str:
        if error_count == 0 and warning_count == 0:
            return "No issues"
        parts = []
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        if warning_count > 0:
            parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        return ", ".join(parts)

    def format_details_terminal(self, result: RunnerResult) -> str:
        ec, wc = self._counts(result)
        text = self._summary_text(ec, wc)
        if ec > 0:
            return f"[red]{text}[/red]"
        if wc > 0:
            return f"[yellow]{text}[/yellow]"
        return f"[green]{text}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        ec, wc = self._counts(result)
        text = self._summary_text(ec, wc)
        if ec > 0:
            return f'<span class="sensors-error">{text}</span>'
        if wc > 0:
            return f'<span class="sensors-warn">{text}</span>'
        return f'<span class="sensors-success">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        ec, wc = self._counts(result)
        return self._summary_text(ec, wc)

    def _violation_lines(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("violations", [])

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        lines: list[str] = []
        if not violations:
            ec, wc = self._counts(result)
            lines.append(f"[red]{ec} errors, {wc} warnings (no details available)[/red]")
        else:
            for v in violations:
                severity = "ERROR" if v.get("severity", 2) == 2 else "WARN"
                color = "red" if severity == "ERROR" else "yellow"
                file_path = v.get("file", "")
                line = v.get("line", 0)
                col = v.get("column", 0)
                rule = v.get("ruleId", "")
                message = v.get("message", "")
                lines.append(
                    f"  [{color}]{file_path}:{line}:{col} {severity}[/{color}] "
                    f"[dim]{rule}[/dim] {message}"
                )
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        if not violations:
            ec, wc = self._counts(result)
            return (
                f'<span class="sensors-error">{ec} errors, {wc} warnings '
                f"(no details available)</span>"
            )
        parts = []
        for v in violations:
            severity = "ERROR" if v.get("severity", 2) == 2 else "WARN"
            css = "sensors-error" if severity == "ERROR" else "sensors-warn"
            file_path = v.get("file", "")
            line = v.get("line", 0)
            col = v.get("column", 0)
            rule = v.get("ruleId", "")
            message = v.get("message", "")
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{file_path}:{line}:{col}</span> '
                f'<span class="{css}">{severity}</span> '
                f'<span class="sensors-rule">{rule}</span> '
                f'<span class="sensors-message">{message}</span>'
                f"</div>"
            )
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        lines: list[str] = []
        if not violations:
            ec, wc = self._counts(result)
            lines.append(f"{ec} errors, {wc} warnings (no details available)")
        else:
            for v in violations:
                severity = "ERROR" if v.get("severity", 2) == 2 else "WARN"
                file_path = v.get("file", "")
                line = v.get("line", 0)
                col = v.get("column", 0)
                rule = v.get("ruleId", "")
                message = v.get("message", "")
                lines.append(f"  {file_path}:{line}:{col} {severity} {rule} {message}")
        return "\n".join(lines)
