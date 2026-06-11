"""Semgrep output parser.

Parses semgrep --json output into structured results.
"""

import json
from typing import Any

from sensors.config import Finding, Metric, ParsedOutput, ScoreInfo

from .base import OutputParser

# Semgrep reports unavailable paid products (e.g. SAST, SCA) as errors when the
# user isn't logged in. PartialParsing occurs when semgrep can't parse a snippet
# inside a rule -- the scan still runs successfully on all other files.
_IGNORABLE_ERROR_TYPES = {"NeedLogin", "SemgrepCoreAccountTierTooLow", "PartialParsing"}


def _error_type(e: dict) -> str:
    t = e.get("type", "")
    # semgrep encodes some error types as ["TypeName", ...data...]
    if isinstance(t, list) and t:
        return str(t[0])
    return str(t)


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

    def parse(self, output: str) -> ParsedOutput:
        """Parse semgrep JSON output into ParsedOutput."""
        try:
            json_str = self._extract_json(output)
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Number of semgrep findings"),
                extra={"parseError": str(e)},
            )

        results = data.get("results", [])
        all_errors = data.get("errors", [])
        errors = [e for e in all_errors if _error_type(e) not in _IGNORABLE_ERROR_TYPES]

        findings = self._build_findings(results)
        finding_count = len(findings)
        error_count = len(errors)

        return ParsedOutput(
            success=finding_count == 0 and error_count == 0,
            summary=self._summary_text(finding_count, error_count),
            score=ScoreInfo(
                value=finding_count + error_count,
                direction="less",
                description="Number of semgrep findings",
            ),
            findings=findings,
            metrics=[
                Metric("findingCount", "Findings", finding_count),
                Metric("errorCount", "Errors", error_count),
            ],
            extra={
                "errors": [
                    {"message": e.get("message", str(e)), "type": _error_type(e)}
                    for e in errors
                ],
            },
        )

    def _build_findings(self, results: list[dict[str, Any]]) -> list[Finding]:
        findings = []
        for r in results:
            extra = r.get("extra", {})
            start = r.get("start", {})
            raw_severity = extra.get("severity", "WARNING").upper()
            findings.append(Finding(
                file=r.get("path", ""),
                line=start.get("line", 0),
                column=start.get("col", 0),
                rule=r.get("check_id", ""),
                message=extra.get("message", ""),
                severity="error" if raw_severity == "ERROR" else "warning",
            ))
        return findings

    def _summary_text(self, finding_count: int, error_count: int) -> str:
        if finding_count == 0 and error_count == 0:
            return "No findings"
        parts = []
        if finding_count > 0:
            parts.append(f"{finding_count} finding{'s' if finding_count != 1 else ''}")
        if error_count > 0:
            parts.append(f"{error_count} error{'s' if error_count != 1 else ''}")
        return ", ".join(parts)
