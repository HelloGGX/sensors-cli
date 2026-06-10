"""ESLint output parser."""

import json
from datetime import datetime
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser


class ESLintParser(OutputParser):
    """Parser for ESLint JSON output."""

    async def parse_output(self, output: str) -> RunnerResult:
        """Parse ESLint --format json output into RunnerResult."""
        try:
            data = json.loads(self._strip_preamble(output))
        except json.JSONDecodeError as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]}
            )

        # ESLint JSON can be a single object or array of results (one per file)
        # It may also contain a top-level "summary" with triggeredRules guidance
        summary = data.get("summary", {}) if isinstance(data, dict) else {}
        triggered_rules: list[dict[str, Any]] = summary.get("triggeredRules", [])

        if isinstance(data, list):
            error_count = sum(f.get("errorCount", 0) for f in data)
            warning_count = sum(f.get("warningCount", 0) for f in data)
            violations = self._extract_violations(data)
        else:
            results: list[dict[str, Any]] = data.get("results") or data.get("files") or []
            error_count = summary.get("totalErrors", data.get("errorCount", sum(f.get("errorCount", 0) for f in results)))
            warning_count = summary.get("totalWarnings", data.get("warningCount", sum(f.get("warningCount", 0) for f in results)))
            violations = self._extract_violations(results if results else [data])

        success = error_count == 0 and warning_count == 0
        out: dict[str, Any] = {
            "errorCount": error_count,
            "warningCount": warning_count,
            "violations": violations,
            "triggeredRules": triggered_rules,
        }
        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output=out,
        )

    def _extract_violations(self, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Extract violation list from ESLint file results."""
        violations: list[dict[str, Any]] = []
        for f in files:
            path = f.get("filePath", "")
            for msg in f.get("messages", []):
                violations.append({
                    "file": path,
                    "line": msg.get("line", 0),
                    "column": msg.get("column", 0),
                    "message": msg.get("message", ""),
                    "severity": msg.get("severity", 2),
                    "ruleId": msg.get("ruleId"),
                    "shortText": msg.get("shortText", ""),
                })
        return violations

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        ec, wc = self._counts(result)
        return ScoreInfo(value=ec + wc, direction="less", description="Number of lint issues")

    @staticmethod
    def _strip_preamble(output: str) -> str:
        """Strip non-JSON preamble (e.g. npm script headers) before the first [ or {."""
        for i, ch in enumerate(output):
            if ch in ("{", "["):
                return output[i:]
        return output

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> tuple:
        output = result.output
        return output.get("errorCount", 0), output.get("warningCount", 0)

    def _triggered_rules(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("triggeredRules", [])

    def _rules_with_guidance(self, result: RunnerResult) -> list[dict[str, Any]]:
        return [r for r in self._triggered_rules(result) if (r.get("guidance") or "").strip()]

    def _summary_text(self, error_count: int, warning_count: int) -> str:
        if error_count == 0 and warning_count == 0:
            return "No issues"
        parts = []
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        if warning_count > 0:
            parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        return ", ".join(parts)

    # -- Details (short one-liner) --

    def format_details_terminal(self, result: RunnerResult) -> str:
        ec, wc = self._counts(result)
        text = self._summary_text(ec, wc)
        rules = self._triggered_rules(result)
        if rules:
            rule_ids = ", ".join(r.get("ruleId", "") for r in rules)
            text = f"{text} • {rule_ids}"
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

    # -- Failures (multi-line) --

    def _violation_lines(self, result: RunnerResult) -> list[dict[str, Any]]:
        return result.output.get("violations", [])

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        lines = []
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
                message = v.get("shortText", "") or v.get("message", "")
                lines.append(
                    f"  [{color}]{file_path}:{line}:{col} {severity}[/{color}] "
                    f"[dim]{rule}[/dim] {message}"
                )
        rules = self._rules_with_guidance(result)
        if rules:
            lines.append("")
            lines.append("  [bold]Triggered rules:[/bold]")
            for r in rules:
                rule_id = r.get("ruleId", "")
                guidance = r.get("guidance", "")
                lines.append(f"  [yellow]{rule_id}[/yellow]")
                for guidance_line in guidance.splitlines():
                    lines.append(f"  [dim]{guidance_line}[/dim]")
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        if not violations:
            ec, wc = self._counts(result)
            return f'<span class="sensors-error">{ec} errors, {wc} warnings (no details available)</span>'
        parts = []
        for v in violations:
            severity = "ERROR" if v.get("severity", 2) == 2 else "WARN"
            css = "sensors-error" if severity == "ERROR" else "sensors-warn"
            file_path = v.get("file", "")
            line = v.get("line", 0)
            col = v.get("column", 0)
            rule = v.get("ruleId", "")
            message = v.get("shortText", "") or v.get("message", "")
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{file_path}:{line}:{col}</span> '
                f'<span class="{css}">{severity}</span> '
                f'<span class="sensors-rule">{rule}</span> '
                f'<span class="sensors-message">{message}</span>'
                f'</div>'
            )
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = self._violation_lines(result)
        lines = []
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
                message = v.get("shortText", "") or v.get("message", "")
                lines.append(f"  {file_path}:{line}:{col} {severity} {rule} {message}")
        rules = self._rules_with_guidance(result)
        if rules:
            lines.append("")
            lines.append("Correction guidance:")
            for r in rules:
                rule_id = r.get("ruleId", "")
                guidance = r.get("guidance", "")
                lines.append(f"  {rule_id}")
                for guidance_line in guidance.splitlines():
                    lines.append(f"  {guidance_line}")
        return "\n".join(lines)
