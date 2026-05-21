"""dependency-cruiser output parser (err-long format)."""

import re
from datetime import datetime
from typing import Any

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser

# Matches: "  error rule-name: source/file.ts → target/file.ts"
# or:      "  warn rule-name: source/file.ts → target/file.ts"
_VIOLATION_RE = re.compile(
    r"^\s+(error|warn)\s+"
    r"([\w-]+):\s+"
    r"(.+?)\s+→\s+(.+?)\s*$",
    re.MULTILINE,
)

# Matches: "x 2 dependency violations (2 errors, 0 warnings). 129 modules, 302 dependencies cruised."
_SUMMARY_RE = re.compile(
    r"(\d+)\s+dependency violation.*?"
    r"\((\d+)\s+error.*?,\s*(\d+)\s+warning",
)


class DepcruiseParser(OutputParser):
    """Parser for dependency-cruiser --output-type err-long output."""

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            return self._do_parse(output)
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def _do_parse(self, output: str) -> RunnerResult:
        violations = self._extract_violations(output)
        summary = _SUMMARY_RE.search(output)

        if summary:
            error_count = int(summary.group(2))
            warning_count = int(summary.group(3))
        else:
            # No summary line → likely clean
            error_count = sum(1 for v in violations if v["severity"] == "error")
            warning_count = sum(1 for v in violations if v["severity"] == "warn")

        success = error_count == 0
        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output={
                "errorCount": error_count,
                "warningCount": warning_count,
                "violations": violations,
            },
        )

    def _extract_violations(self, output: str) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        lines = output.split("\n")

        i = 0
        while i < len(lines):
            m = _VIOLATION_RE.match(lines[i])
            if m:
                severity, rule, source, target = m.group(1), m.group(2), m.group(3), m.group(4)
                # Collect indented description lines
                desc_lines: list[str] = []
                j = i + 1
                while j < len(lines) and lines[j].startswith("    "):
                    desc_lines.append(lines[j].strip())
                    j += 1
                violations.append({
                    "severity": severity,
                    "rule": rule,
                    "source": source,
                    "target": target,
                    "message": " ".join(desc_lines),
                })
                i = j
            else:
                i += 1

        return violations

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        ec = result.output.get("errorCount", 0)
        wc = result.output.get("warningCount", 0)
        return ScoreInfo(value=ec + wc, direction="less", description="Number of dependency violations")

    # -- Helpers --

    def _counts(self, result: RunnerResult) -> tuple:
        return result.output.get("errorCount", 0), result.output.get("warningCount", 0)

    def _summary_text(self, ec: int, wc: int) -> str:
        if ec == 0 and wc == 0:
            return "No violations"
        parts = []
        if ec > 0:
            parts.append(f"{ec} error{'s' if ec != 1 else ''}")
        if wc > 0:
            parts.append(f"{wc} warning{'s' if wc != 1 else ''}")
        return ", ".join(parts)

    # -- Details (short one-liner) --

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

    # -- Failures (multi-line) --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            ec, wc = self._counts(result)
            return f"[red]{ec} errors, {wc} warnings (no details available)[/red]"
        lines = []
        for v in violations:
            color = "red" if v["severity"] == "error" else "yellow"
            lines.append(
                f"  [{color}]{v['severity'].upper()}[/{color}] "
                f"[dim]{v['rule']}[/dim] "
                f"{v['source']} → {v['target']}"
            )
            if v.get("message"):
                lines.append(f"    [dim]{v['message']}[/dim]")
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            ec, wc = self._counts(result)
            return f'<span class="sensors-error">{ec} errors, {wc} warnings (no details available)</span>'
        parts = []
        for v in violations:
            css = "sensors-error" if v["severity"] == "error" else "sensors-warn"
            parts.append(
                f'<div class="sensors-violation">'
                f'<span class="{css}">{v["severity"].upper()}</span> '
                f'<span class="sensors-rule">{v["rule"]}</span> '
                f'<span class="sensors-file">{v["source"]}</span> → '
                f'<span class="sensors-file">{v["target"]}</span>'
                f'<div class="sensors-message">{v.get("message", "")}</div>'
                f'</div>'
            )
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            ec, wc = self._counts(result)
            return f"{ec} errors, {wc} warnings (no details available)"
        lines = []
        for v in violations:
            lines.append(
                f"  {v['severity'].upper()} {v['rule']}: {v['source']} -> {v['target']}"
            )
            if v.get("message"):
                lines.append(f"    {v['message']}")
        return "\n".join(lines)
