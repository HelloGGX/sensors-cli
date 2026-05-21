"""Ruff output parser."""

import re
from datetime import datetime
from typing import Any

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser


class RuffParser(OutputParser):
    """Parser for ruff check text output."""

    _VIOLATION_RE = re.compile(
        r'^(\w+)\s+(.+?)\n\s+-->\s+(.+?):(\d+):(\d+)',
        re.MULTILINE,
    )
    _SUMMARY_RE = re.compile(r'Found (\d+) errors?')

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            violations = []
            for m in self._VIOLATION_RE.finditer(output):
                violations.append({
                    "rule": m.group(1),
                    "message": m.group(2),
                    "file": m.group(3),
                    "line": int(m.group(4)),
                    "column": int(m.group(5)),
                })

            summary_m = self._SUMMARY_RE.search(output)
            error_count = int(summary_m.group(1)) if summary_m else len(violations)

            return RunnerResult(
                timestamp=datetime.now(),
                success=error_count == 0,
                output={
                    "errorCount": error_count,
                    "violations": violations,
                },
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        count = result.output.get("errorCount", 0)
        return ScoreInfo(
            value=count,
            direction="less",
            description="Number of ruff lint issues",
        )

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> int:
        return result.output.get("errorCount", 0)

    def _summary_text(self, count: int) -> str:
        if count == 0:
            return "No issues"
        return f"{count} issue{'s' if count != 1 else ''}"

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        count = self._counts(result)
        text = self._summary_text(count)
        if count > 0:
            return f"[red]{text}[/red]"
        return f"[green]{text}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        count = self._counts(result)
        text = self._summary_text(count)
        if count > 0:
            return f'<span class="sensors-error">{text}</span>'
        return f'<span class="sensors-success">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._summary_text(self._counts(result))

    # -- Failures (multi-line) --

    def _violation_lines(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("violations", [])

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        if not violations:
            return f"[red]{self._counts(result)} issues (no details)[/red]"
        lines = []
        for v in violations:
            lines.append(
                f"  [red]{v['file']}:{v['line']}:{v['column']}[/red] "
                f"[dim]{v['rule']}[/dim] {v['message']}"
            )
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        if not violations:
            return (
                f'<span class="sensors-error">'
                f'{self._counts(result)} issues (no details)</span>'
            )
        parts = []
        for v in violations:
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{v["file"]}:{v["line"]}:{v["column"]}</span> '
                f'<span class="sensors-rule">{v["rule"]}</span> '
                f'<span class="sensors-message">{v["message"]}</span>'
                f'</div>'
            )
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        if not violations:
            return f"{self._counts(result)} issues (no details)"
        lines = []
        for v in violations:
            lines.append(
                f"  {v['file']}:{v['line']}:{v['column']} "
                f"{v['rule']} {v['message']}"
            )
        return "\n".join(lines)
