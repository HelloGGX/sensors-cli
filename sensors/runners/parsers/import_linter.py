"""import-linter output parser."""

import re
from datetime import datetime
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

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

        success = broken_count == 0
        return RunnerResult(
            timestamp=datetime.now(),
            success=success,
            output={
                "keptCount": kept_count,
                "brokenCount": broken_count,
                "contracts": contracts,
                "violations": violations,
            },
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

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        broken = result.output.get("brokenCount", 0)
        return ScoreInfo(
            value=broken,
            direction="less",
            description="Number of broken import contracts",
        )

    # -- Details (short one-liner) --

    def _summary_text(self, result: RunnerResult) -> str:
        kept = result.output.get("keptCount", 0)
        broken = result.output.get("brokenCount", 0)
        total = kept + broken
        if broken == 0:
            return f"All {total} contracts kept"
        return f"{broken} broken, {kept} kept"

    def format_details_terminal(self, result: RunnerResult) -> str:
        text = self._summary_text(result)
        if result.success:
            return f"[green]{text}[/green]"
        return f"[red]{text}[/red]"

    def format_details_html(self, result: RunnerResult) -> str:
        text = self._summary_text(result)
        css = "sensors-success" if result.success else "sensors-error"
        return f'<span class="{css}">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._summary_text(result)

    # -- Failures (multi-line) --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            broken = result.output.get("brokenCount", 0)
            return f"[red]{broken} broken contract(s) (no details available)[/red]"
        lines = []
        current_contract = None
        for v in violations:
            if v["contract"] != current_contract:
                current_contract = v["contract"]
                lines.append(f"  [red]BROKEN[/red] [dim]{v['contract']}[/dim]")
                lines.append(f"    [dim]{v['description']}[/dim]")
            loc = f" (l.{v['line']})" if v.get("line") else ""
            lines.append(f"    [yellow]{v['source']}[/yellow] -> [yellow]{v['target']}[/yellow]{loc}")
        return "\n".join(lines)

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            broken = result.output.get("brokenCount", 0)
            return f'<span class="sensors-error">{broken} broken contract(s) (no details available)</span>'
        parts = []
        current_contract = None
        for v in violations:
            if v["contract"] != current_contract:
                current_contract = v["contract"]
                parts.append(
                    f'<div class="sensors-violation">'
                    f'<span class="sensors-error">BROKEN</span> '
                    f'<span class="sensors-rule">{v["contract"]}</span>'
                    f'<div class="sensors-message">{v["description"]}</div>'
                )
            loc = f" (l.{v['line']})" if v.get("line") else ""
            parts.append(
                f'<div class="sensors-file">{v["source"]} '
                f'&rarr; {v["target"]}{loc}</div>'
            )
        if violations:
            parts.append("</div>")
        return "\n".join(parts)

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        violations = result.output.get("violations", [])
        if not violations:
            broken = result.output.get("brokenCount", 0)
            return f"{broken} broken contract(s) (no details available)"
        lines = []
        current_contract = None
        for v in violations:
            if v["contract"] != current_contract:
                current_contract = v["contract"]
                lines.append(f"BROKEN: {v['contract']}")
                lines.append(f"  {v['description']}")
            loc = f" (l.{v['line']})" if v.get("line") else ""
            lines.append(f"  {v['source']} -> {v['target']}{loc}")
        return "\n".join(lines)
