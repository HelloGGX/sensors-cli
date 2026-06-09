"""Ruff output parser."""

import json
import re
from datetime import datetime
from html import escape
from typing import Any

from sensors.config import RunnerResult, ScoreInfo

from .base import OutputParser

GUIDANCE_JSON_KEY = "sensors_rule_guidance"


class RuffParser(OutputParser):
    """Parser for ruff check text or JSON output (with optional rule guidance)."""

    _VIOLATION_RE = re.compile(
        r'^(\w+)\s+(.+?)\n\s+-->\s+(.+?):(\d+):(\d+)',
        re.MULTILINE,
    )
    _SUMMARY_RE = re.compile(r'Found (\d+) errors?')

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            text = output.strip()
            if text.startswith("["):
                return self._parse_json_output(self._strip_preamble(text))
            return self._parse_text_output(output)
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def _parse_json_output(self, output: str) -> RunnerResult:
        items = json.loads(output)
        if not isinstance(items, list):
            raise ValueError("Expected JSON array from ruff")

        diagnostics, guidance_block = self._split_json_output(items)
        violations = self._violations_from_diagnostics(diagnostics)
        error_count = len(violations)

        out: dict[str, Any] = {
            "errorCount": error_count,
            "violations": violations,
        }
        if guidance_block:
            out["ruleGuidance"] = guidance_block

        return RunnerResult(
            timestamp=datetime.now(),
            success=error_count == 0,
            output=out,
        )

    def _parse_text_output(self, output: str) -> RunnerResult:
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

    @staticmethod
    def _strip_preamble(output: str) -> str:
        for i, ch in enumerate(output):
            if ch == "[":
                return output[i:]
        return output

    @staticmethod
    def _split_json_output(
        items: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if items and isinstance(items[-1], dict) and GUIDANCE_JSON_KEY in items[-1]:
            block = items[-1][GUIDANCE_JSON_KEY]
            return items[:-1], block if isinstance(block, dict) else None
        return items, None

    @staticmethod
    def _violations_from_diagnostics(
        diagnostics: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        violations: list[dict[str, Any]] = []
        for d in diagnostics:
            if not isinstance(d, dict) or "code" not in d:
                continue
            loc = d.get("location") or {}
            violations.append({
                "rule": str(d.get("code", "")),
                "message": str(d.get("message", "")),
                "file": str(d.get("filename", "")),
                "line": int(loc.get("row", 0)),
                "column": int(loc.get("column", 0)),
            })
        return violations

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

    def _rule_guidance_block(self, result: RunnerResult) -> dict[str, Any] | None:
        block = result.output.get("ruleGuidance")
        return block if isinstance(block, dict) else None

    def _rules_by_code(self, result: RunnerResult) -> dict[str, dict[str, str]]:
        block = self._rule_guidance_block(result)
        if not block:
            return {}
        rules = block.get("rules")
        if not isinstance(rules, dict):
            return {}
        return {
            str(code): {
                "short": str(entry.get("short", "")),
                "guidance": str(entry.get("guidance", "")),
            }
            for code, entry in rules.items()
            if isinstance(entry, dict)
        }

    def _triggered_codes(self, result: RunnerResult) -> list[str]:
        block = self._rule_guidance_block(result)
        if not block:
            return []
        triggered = block.get("triggered")
        if not isinstance(triggered, list):
            return []
        return [str(code) for code in triggered]

    def _triggered_with_guidance(
        self, result: RunnerResult
    ) -> list[tuple[str, dict[str, str]]]:
        rules = self._rules_by_code(result)
        out: list[tuple[str, dict[str, str]]] = []
        for code in self._triggered_codes(result):
            entry = rules.get(code)
            if entry and entry.get("guidance", "").strip():
                out.append((code, entry))
        return out

    def _guidance_for_rule(
        self, result: RunnerResult, rule: str
    ) -> dict[str, str] | None:
        entry = self._rules_by_code(result).get(rule)
        if not entry or not entry.get("guidance", "").strip():
            return None
        return entry

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
        triggered = self._triggered_with_guidance(result)
        if triggered:
            lines.append("")
            lines.append("  [bold]Rule guidance:[/bold]")
            for code, entry in triggered:
                short = entry.get("short", "").strip()
                if short:
                    lines.append(f"  [yellow]{code}[/yellow] — {short}")
                else:
                    lines.append(f"  [yellow]{code}[/yellow]")
                for guidance_line in entry["guidance"].splitlines():
                    lines.append(f"  [dim]{guidance_line}[/dim]")
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
            rule = v["rule"]
            guidance = self._guidance_for_rule(result, rule)
            html = (
                f'<div class="sensors-violation">'
                f'<span class="sensors-file">{escape(v["file"])}:{v["line"]}:{v["column"]}</span> '
                f'<span class="sensors-rule">{escape(rule)}</span> '
                f'<span class="sensors-message">{escape(v["message"])}</span>'
            )
            if guidance:
                html += (
                    '<div class="sensors-guidance">'
                    + "<br>".join(
                        escape(line) for line in guidance["guidance"].splitlines()
                    )
                    + "</div>"
                )
            html += "</div>"
            parts.append(html)
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
        triggered = self._triggered_with_guidance(result)
        if triggered:
            lines.append("")
            lines.append("Correction guidance:")
            for code, entry in triggered:
                short = entry.get("short", "").strip()
                if short:
                    lines.append(f"  {code} — {short}")
                else:
                    lines.append(f"  {code}")
                for guidance_line in entry["guidance"].splitlines():
                    lines.append(f"  {guidance_line}")
        return "\n".join(lines)
