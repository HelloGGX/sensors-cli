"""ESLint output parser."""

import json
from typing import Any

from sensors.config import Finding, GuidanceBlock, Metric, ParsedOutput, ScoreInfo

from .base import OutputParser


class ESLintParser(OutputParser):
    """Parser for ESLint JSON output."""

    def parse(self, output: str) -> ParsedOutput:
        """Parse ESLint --format json output into ParsedOutput."""
        try:
            data = json.loads(self._strip_preamble(output))
        except json.JSONDecodeError as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Number of lint issues"),
                extra={"parseError": str(e)},
            )

        error_count, warning_count, file_list, triggered_rules_raw = self._unpack_data(data)
        findings = self._build_findings(file_list)
        guidance = self._build_guidance(triggered_rules_raw)

        return ParsedOutput(
            success=error_count == 0 and warning_count == 0,
            summary=self._summary_text(error_count, warning_count),
            score=ScoreInfo(
                value=error_count + warning_count,
                direction="less",
                description="Number of lint issues",
            ),
            findings=findings,
            metrics=[
                Metric("errorCount", "Errors", error_count),
                Metric("warningCount", "Warnings", warning_count),
            ],
            guidance=guidance,
        )

    def _unpack_data(
        self, data: list[dict[str, Any]] | dict[str, Any]
    ) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
        """Return (error_count, warning_count, file_list, triggered_rules) from raw JSON."""
        summary_block = data.get("summary", {}) if isinstance(data, dict) else {}
        triggered_rules_raw: list[dict[str, Any]] = summary_block.get("triggeredRules", [])

        if isinstance(data, list):
            error_count = sum(f.get("errorCount", 0) for f in data)
            warning_count = sum(f.get("warningCount", 0) for f in data)
            file_list = data
        else:
            results: list[dict[str, Any]] = data.get("results") or data.get("files") or []
            error_count = summary_block.get(
                "totalErrors",
                data.get("errorCount", sum(f.get("errorCount", 0) for f in results)),
            )
            warning_count = summary_block.get(
                "totalWarnings",
                data.get("warningCount", sum(f.get("warningCount", 0) for f in results)),
            )
            file_list = results if results else [data]

        return error_count, warning_count, file_list, triggered_rules_raw

    def _build_findings(self, file_list: list[dict[str, Any]]) -> list[Finding]:
        """Build Finding objects from ESLint file results."""
        findings = []
        for f in file_list:
            for msg in f.get("messages", []):
                findings.append(Finding(
                    file=f.get("filePath", ""),
                    line=msg.get("line", 0),
                    column=msg.get("column", 0),
                    message=msg.get("shortText", "") or msg.get("message", ""),
                    rule=msg.get("ruleId"),
                    severity="error" if msg.get("severity", 2) == 2 else "warning",
                ))
        return findings

    def _build_guidance(self, triggered_rules_raw: list[dict[str, Any]]) -> list[GuidanceBlock]:
        """Build GuidanceBlock objects from triggered rules that have guidance text."""
        return [
            GuidanceBlock(rule=r["ruleId"], body=r.get("guidance", ""))
            for r in triggered_rules_raw
            if r.get("guidance", "").strip()
        ]

    @staticmethod
    def _strip_preamble(output: str) -> str:
        """Strip non-JSON preamble (e.g. npm script headers) before the first [ or {."""
        for i, ch in enumerate(output):
            if ch in ("{", "["):
                return output[i:]
        return output

    # -- Helpers --

    def _summary_text(self, error_count: int, warning_count: int) -> str:
        if error_count == 0 and warning_count == 0:
            return "No issues"
        parts = []
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        if warning_count > 0:
            parts.append(f"{warning_count} warning{'s' if warning_count != 1 else ''}")
        return ", ".join(parts)
