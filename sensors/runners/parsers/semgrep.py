"""Semgrep output parser.

Parses semgrep --json output into structured results.
"""

import json
from datetime import datetime
from typing import Any

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser


class SemgrepParser(OutputParser):
    """Parser for semgrep --json output."""

    @staticmethod
    def _extract_json(output: str) -> str:
        """Extract the JSON object from mixed output.

        Semgrep sends progress/summary to stderr and JSON to stdout,
        but the runner captures both together. Find the outermost { } block.
        """
        start = output.find("{")
        if start == -1:
            return output
        end = output.rfind("}")
        if end == -1:
            return output
        return output[start:end + 1]

    async def parse_output(self, output: str) -> RunnerResult:
        """Parse semgrep JSON output into RunnerResult."""
        try:
            json_str = self._extract_json(output)
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

        results = data.get("results", [])
        all_errors = data.get("errors", [])

        # Semgrep reports unavailable paid products (e.g. SAST, SCA) as errors
        # when the user isn't logged in. These are not real scan errors.
        # PartialParsing occurs when semgrep can't parse a snippet inside a rule
        # (e.g. GitHub Actions YAML with Bash metavariable-pattern) -- the scan
        # still runs successfully on all other files.
        _IGNORABLE_ERROR_TYPES = {"NeedLogin", "SemgrepCoreAccountTierTooLow", "PartialParsing"}
        def _error_type(e: dict) -> str:
            t = e.get("type", "")
            # semgrep encodes some error types as ["TypeName", ...data...]
            if isinstance(t, list) and t:
                return str(t[0])
            return str(t)

        errors = [
            e for e in all_errors
            if _error_type(e) not in _IGNORABLE_ERROR_TYPES
        ]

        violations = self._extract_violations(results)
        finding_count = len(violations)

        return RunnerResult(
            timestamp=datetime.now(),
            success=finding_count == 0 and len(errors) == 0,
            output={
                "findingCount": finding_count,
                "errorCount": len(errors),
                "violations": violations,
                "errors": [
                    {"message": e.get("message", str(e)), "type": _error_type(e)}
                    for e in errors
                ],
            },
        )

    def _extract_violations(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract violation list from semgrep results."""
        violations: list[dict[str, Any]] = []
        for r in results:
            extra = r.get("extra", {})
            start = r.get("start", {})
            end = r.get("end", {})
            violations.append({
                "file": r.get("path", ""),
                "line": start.get("line", 0),
                "column": start.get("col", 0),
                "endLine": end.get("line", 0),
                "endColumn": end.get("col", 0),
                "ruleId": r.get("check_id", ""),
                "message": extra.get("message", ""),
                "severity": extra.get("severity", "WARNING"),
            })
        return violations

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        count = result.output.get("findingCount", 0) + result.output.get("errorCount", 0)
        return ScoreInfo(
            value=count,
            direction="less",
            description="Number of semgrep findings",
        )

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> tuple:
        return result.output.get("findingCount", 0), result.output.get("errorCount", 0)

    def _summary_text(self, finding_count: int, error_count: int) -> str:
        if finding_count == 0 and error_count == 0:
            return "No findings"
        parts = []
        if finding_count > 0:
            parts.append(f"{finding_count} finding{'s' if finding_count != 1 else ''}")
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        return ", ".join(parts)

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        fc, ec = self._counts(result)
        text = self._summary_text(fc, ec)
        if fc > 0 or ec > 0:
            return f"[red]{text}[/red]"
        return f"[green]{text}[/green]"

    def format_details_html(self, result: RunnerResult) -> str:
        fc, ec = self._counts(result)
        text = self._summary_text(fc, ec)
        if fc > 0 or ec > 0:
            return f'<span class="sensors-error">{text}</span>'
        return f'<span class="sensors-success">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        fc, ec = self._counts(result)
        return self._summary_text(fc, ec)

    # -- Failures (multi-line) --

    def _violation_lines(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("violations", [])

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        errors = result.output.get("errors", [])
        lines = []
        for v in violations:
            severity = v.get("severity", "WARNING").upper()
            color = "red" if severity == "ERROR" else "yellow"
            lines.append(
                f"  [{color}]{v['file']}:{v['line']}:{v['column']} {severity}[/{color}] "
                f"[dim]{v['ruleId']}[/dim] {v['message']}"
            )
        for e in errors:
            lines.append(f"  [red]error ({e.get('type', 'unknown')})[/red] {e.get('message', '')}")
        if not lines:
            fc, ec = self._counts(result)
            return f"[red]{self._summary_text(fc, ec)} (no details available)[/red]"
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        errors = result.output.get("errors", [])
        parts = []
        for v in violations:
            severity = v.get("severity", "WARNING").upper()
            css = "sensors-error" if severity == "ERROR" else "sensors-warn"
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{v["file"]}:{v["line"]}:{v["column"]}</span> '
                f'<span class="{css}">{severity}</span> '
                f'<span class="sensors-rule">{v["ruleId"]}</span> '
                f'<span class="sensors-message">{v["message"]}</span>'
                f'</div>'
            )
        for e in errors:
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-error">error ({e.get("type", "unknown")})</span> '
                f'<span class="sensors-message">{e.get("message", "")}</span>'
                f'</div>'
            )
        if not parts:
            fc, ec = self._counts(result)
            return f'<span class="sensors-error">{self._summary_text(fc, ec)} (no details available)</span>'
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        errors = result.output.get("errors", [])
        lines = []
        for v in violations:
            severity = v.get("severity", "WARNING").upper()
            lines.append(
                f"  {v['file']}:{v['line']}:{v['column']} {severity} {v['ruleId']} {v['message']}"
            )
        for e in errors:
            lines.append(f"  error ({e.get('type', 'unknown')}) {e.get('message', '')}")
        if not lines:
            fc, ec = self._counts(result)
            return f"{self._summary_text(fc, ec)} (no details available)"
        return "\n".join(lines)
