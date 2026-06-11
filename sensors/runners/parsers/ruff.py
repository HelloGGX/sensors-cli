"""Ruff output parser."""

import json
import re
from typing import Any

from sensors.config import Finding, GuidanceBlock, Metric, ParsedOutput, ScoreInfo

from .base import OutputParser

GUIDANCE_JSON_KEY = "sensors_rule_guidance"


class RuffParser(OutputParser):
    """Parser for ruff check text or JSON output (with optional rule guidance)."""

    _VIOLATION_RE = re.compile(
        r'^(\w+)\s+(.+?)\n\s+-->\s+(.+?):(\d+):(\d+)',
        re.MULTILINE,
    )
    _SUMMARY_RE = re.compile(r'Found (\d+) errors?')

    def parse(self, output: str) -> ParsedOutput:
        try:
            text = output.strip()
            if text.startswith("["):
                return self._parse_json_output(self._strip_preamble(text))
            return self._parse_text_output(output)
        except Exception as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(
                    value=0,
                    direction="less",
                    description="Number of ruff lint issues",
                ),
                extra={"parseError": str(e), "raw": output[:500]},
            )

    def _parse_json_output(self, output: str) -> ParsedOutput:
        items = json.loads(output)
        if not isinstance(items, list):
            raise ValueError("Expected JSON array from ruff")

        diagnostics, guidance_block = self._split_json_output(items)
        violations = self._violations_from_diagnostics(diagnostics)
        return self._build_parsed_output(violations, guidance_block)

    def _parse_text_output(self, output: str) -> ParsedOutput:
        violations = []
        for m in self._VIOLATION_RE.finditer(output):
            violations.append({
                "rule": m.group(1),
                "message": m.group(2),
                "file": m.group(3),
                "line": int(m.group(4)),
                "column": int(m.group(5)),
            })

        return self._build_parsed_output(violations, None)

    def _build_parsed_output(
        self,
        violations: list[dict[str, Any]],
        guidance_block: dict[str, Any] | None,
    ) -> ParsedOutput:
        findings = [
            Finding(
                rule=v.get("rule"),
                message=v.get("message", ""),
                file=v.get("file"),
                line=v.get("line"),
                column=v.get("column"),
                severity="error",
            )
            for v in violations
        ]
        guidance = self._guidance_blocks(guidance_block)
        error_count = len(findings)
        return ParsedOutput(
            success=error_count == 0,
            summary="No issues" if error_count == 0 else f"{error_count} issue{'s' if error_count != 1 else ''}",
            score=ScoreInfo(
                value=error_count,
                direction="less",
                description="Number of ruff lint issues",
            ),
            findings=findings,
            metrics=[Metric("errorCount", "Errors", error_count)],
            guidance=guidance,
            extra={"ruleGuidance": guidance_block} if guidance_block else {},
        )

    def _guidance_blocks(self, guidance_block: dict[str, Any] | None) -> list[GuidanceBlock]:
        if not isinstance(guidance_block, dict):
            return []
        rules = guidance_block.get("rules")
        triggered = guidance_block.get("triggered")
        if not isinstance(rules, dict) or not isinstance(triggered, list):
            return []
        blocks: list[GuidanceBlock] = []
        for code in triggered:
            entry = rules.get(code)
            if not isinstance(entry, dict):
                continue
            body = str(entry.get("guidance", "")).strip()
            if not body:
                continue
            summary = str(entry.get("short", "")).strip() or None
            blocks.append(GuidanceBlock(rule=str(code), body=body, summary=summary))
        return blocks

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

