"""dependency-cruiser output parser (err-long format)."""

import re

from sensors.config import Finding, Metric, ScoreInfo, SensorReading

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

    def parse(self, output: str) -> SensorReading:
        try:
            violations = self._extract_violations(output)
            summary_match = _SUMMARY_RE.search(output)
            if summary_match:
                error_count = int(summary_match.group(2))
                warning_count = int(summary_match.group(3))
            else:
                error_count = sum(1 for v in violations if v["severity"] == "error")
                warning_count = sum(1 for v in violations if v["severity"] == "warn")

            findings = [
                Finding(
                    file=f"{v['source']} \u2192 {v['target']}",
                    rule=v["rule"],
                    severity="error" if v["severity"] == "error" else "warning",
                    message=v["message"] or "",
                )
                for v in violations
            ]

            return SensorReading(
                success=error_count == 0,
                summary=self._summary_text(error_count, warning_count),
                score=ScoreInfo(
                    value=error_count + warning_count,
                    direction="less",
                    description="Number of dependency violations",
                ),
                findings=findings,
                metrics=[
                    Metric("errorCount", "Errors", error_count),
                    Metric("warningCount", "Warnings", warning_count),
                ],
            )
        except Exception as e:
            return SensorReading(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="less", description="Number of dependency violations"),
                extra={"parseError": str(e)},
            )

    def _extract_violations(self, output: str) -> list[dict]:
        violations: list[dict] = []
        lines = output.split("\n")

        i = 0
        while i < len(lines):
            m = _VIOLATION_RE.match(lines[i])
            if m:
                severity, rule, source, target = m.group(1), m.group(2), m.group(3), m.group(4)
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

    def _summary_text(self, ec: int, wc: int) -> str:
        if ec == 0 and wc == 0:
            return "No violations"
        parts = []
        if ec > 0:
            parts.append(f"{ec} error{'s' if ec != 1 else ''}")
        if wc > 0:
            parts.append(f"{wc} warning{'s' if wc != 1 else ''}")
        return ", ".join(parts)
