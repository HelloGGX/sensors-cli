"""Stylelint output parser (--formatter json)."""

import json
from typing import Any

from sensors.config import Finding, Metric, ScoreInfo, SensorReading

from .base import OutputParser


class StylelintParser(OutputParser):
    """Parser for Stylelint JSON formatter output."""

    def parse(self, output: str) -> SensorReading:
        try:
            data = json.loads(self._strip_preamble(output))
        except json.JSONDecodeError as e:
            return SensorReading(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Number of stylelint issues"),
                extra={"parseError": str(e), "raw": output[:500]},
            )

        if not isinstance(data, list):
            return SensorReading(
                success=False,
                summary="Parse error: Expected top-level JSON array from stylelint",
                score=ScoreInfo(value=0, direction="less", description="Number of stylelint issues"),
                extra={
                    "parseError": "Expected top-level JSON array from stylelint",
                    "raw": output[:500],
                },
            )

        findings = self._extract_findings(data)
        error_count = sum(1 for f in findings if f.severity == "error")
        warning_count = sum(1 for f in findings if f.severity == "warning")
        success = error_count == 0 and warning_count == 0

        return SensorReading(
            success=success,
            summary=self._summary_text(error_count, warning_count),
            score=ScoreInfo(
                value=error_count + warning_count,
                direction="less",
                description="Number of stylelint issues",
            ),
            findings=findings,
            metrics=[
                Metric("errorCount", "Errors", error_count),
                Metric("warningCount", "Warnings", warning_count),
            ],
        )

    def _extract_findings(self, files: list[dict[str, Any]]) -> list[Finding]:
        findings: list[Finding] = []
        for f in files:
            path = f.get("source", "")
            for msg in f.get("warnings", []):
                sev_raw = str(msg.get("severity", "error")).lower()
                findings.append(
                    Finding(
                        file=path,
                        line=msg.get("line", 0),
                        column=msg.get("column", 0),
                        message=msg.get("text", ""),
                        severity="error" if sev_raw == "error" else "warning",
                        rule=msg.get("rule", ""),
                    )
                )
            for err in f.get("parseErrors", []):
                findings.append(
                    Finding(
                        file=path,
                        line=err.get("line", 0),
                        column=err.get("column", 0),
                        message=err.get("text", "parse error"),
                        severity="error",
                        rule=err.get("stylelintType", "parse-error"),
                    )
                )
        return findings

    @staticmethod
    def _strip_preamble(output: str) -> str:
        for i, ch in enumerate(output):
            if ch == "[":
                return output[i:]
        return output

    def _summary_text(self, error_count: int, warning_count: int) -> str:
        if error_count == 0 and warning_count == 0:
            return "No issues"
        parts = []
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        if warning_count > 0:
            parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        return ", ".join(parts)
