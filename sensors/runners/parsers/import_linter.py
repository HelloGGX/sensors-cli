"""import-linter output parser."""

import re
from typing import Any

from sensors.config import Finding, Metric, ParsedOutput, ScoreInfo

from .base import OutputParser

# "Contracts: 2 kept, 1 broken."
_SUMMARY_RE = re.compile(r"Contracts:\s+(\d+)\s+kept,\s+(\d+)\s+broken")

# "Contract Name KEPT" or "Contract Name BROKEN" (in the summary table)
_CONTRACT_STATUS_RE = re.compile(r"^(.+?)\s+(KEPT|BROKEN)$", re.MULTILINE)

# Broken contract block: name + dashes + description line + violation lines
_BROKEN_BLOCK_RE = re.compile(
    r"^([^\n]+)\n-+\n\n(.+?):\n\n((?:[ \t]*-[ \t]+[^\n]+\n?)+)",
    re.MULTILINE,
)

# Individual violation: "-   source.module -> target.module (l.19)"
_VIOLATION_ITEM_RE = re.compile(r"-\s+(\S+)\s+->\s+(\S+)(?:\s+\(l\.(\d+)\))?")


class ImportLinterParser(OutputParser):
    """Parser for import-linter (lint-imports) output."""

    def parse(self, output: str) -> ParsedOutput:
        try:
            return self._do_parse(output)
        except Exception as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(
                    value=0,
                    direction="less",
                    description="Number of broken import contracts",
                ),
                extra={"parseError": str(e), "raw": output[:500]},
            )

    def _do_parse(self, output: str) -> ParsedOutput:
        summary = _SUMMARY_RE.search(output)
        if summary:
            kept_count = int(summary.group(1))
            broken_count = int(summary.group(2))
        else:
            kept_count = 0
            broken_count = 0

        contracts = [
            {"name": m.group(1), "status": m.group(2)}
            for m in _CONTRACT_STATUS_RE.finditer(output)
        ]

        violations = self._parse_violations(output)

        # Fall back to counting from violations if summary is missing
        if not summary:
            broken_count = len({v["contract"] for v in violations})
            kept_count = sum(1 for c in contracts if c["status"] == "KEPT")

        findings = []
        for v in violations:
            line = int(v["line"]) if v.get("line") else None
            findings.append(Finding(
                file=f"{v['source']} -> {v['target']}",
                line=line,
                rule=v["contract"],
                message="Forbidden dependency",
                context=v["description"],
                severity="error",
            ))

        total = kept_count + broken_count
        summary_text = (
            f"All {total} contracts kept"
            if broken_count == 0
            else f"{broken_count} broken, {kept_count} kept"
        )
        return ParsedOutput(
            success=broken_count == 0,
            summary=summary_text,
            score=ScoreInfo(
                value=broken_count,
                direction="less",
                description="Number of broken import contracts",
            ),
            findings=findings,
            metrics=[
                Metric("keptCount", "Kept", kept_count),
                Metric("brokenCount", "Broken", broken_count),
            ],
            extra={"contracts": contracts},
        )

    def _parse_violations(self, output: str) -> list[dict[str, Any]]:
        broken_start = output.find("Broken contracts\n")
        if broken_start == -1:
            return []
        section = output[broken_start:]

        violations: list[dict[str, Any]] = []
        for block in _BROKEN_BLOCK_RE.finditer(section):
            contract_name = block.group(1).strip()
            description = block.group(2).strip()
            items_text = block.group(3)
            for item in _VIOLATION_ITEM_RE.finditer(items_text):
                violations.append({
                    "contract": contract_name,
                    "description": description,
                    "source": item.group(1),
                    "target": item.group(2),
                    "line": item.group(3),
                })
        return violations

