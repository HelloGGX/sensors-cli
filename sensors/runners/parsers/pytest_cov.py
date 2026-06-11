"""Pytest-cov coverage output parser."""

import re
from typing import Any

from sensors.config import Metric, ParsedOutput, ScoreInfo

from .base import OutputParser


class PytestCovParser(OutputParser):
    """Parser for pytest-cov coverage report output."""

    def parse(self, output: str) -> ParsedOutput:
        try:
            text = output.strip()
            files = self._parse_coverage_table(text)
            total = self._parse_total(text)
            num_passed, num_failed, num_skipped = self._parse_test_summary(text)
            cov_fail = self._parse_coverage_failure(text)

            success = num_failed == 0 and cov_fail is None

            summary_parts = [f"{total.get('cover', 0)}% coverage"]
            if num_failed:
                summary_parts.append(f"{num_failed} failed")
            summary_parts.append(f"{num_passed} passed")

            return ParsedOutput(
                success=success,
                summary=", ".join(summary_parts),
                score=ScoreInfo(
                    value=total.get("cover", 0),
                    direction="more",
                    description="Test coverage percentage",
                ),
                metrics=[
                    Metric(
                        "totalCoverage",
                        "Coverage",
                        total.get("cover", 0),
                        unit="%",
                        direction="more",
                    ),
                    Metric("passed", "Passed", num_passed, direction="more"),
                    Metric("failed", "Failed", num_failed),
                    Metric("misses", "Misses", total.get("miss", 0)),
                ],
                extra={
                    "totalStatements": total.get("stmts", 0),
                    "numSkipped": num_skipped,
                    "files": files,
                    "coverageFailure": cov_fail,
                    "summary": self._extract_summary(text),
                },
            )
        except Exception as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(
                    value=0,
                    direction="more",
                    description="Test coverage percentage",
                ),
                extra={"parseError": str(e), "raw": output[:500]},
            )

    def _parse_coverage_table(self, text: str) -> list[dict[str, Any]]:
        files = []
        for m in re.finditer(
            r'^(\S+\.py)\s+(\d+)\s+(\d+)\s+(\d+)%(?:[ \t]+(.*))?$',
            text,
            re.MULTILINE,
        ):
            name, stmts, miss, cover, missing = m.groups()
            if name == "TOTAL":
                continue
            files.append({
                "name": name,
                "stmts": int(stmts),
                "miss": int(miss),
                "cover": int(cover),
                "missing": (missing or "").strip(),
            })
        return files

    def _parse_total(self, text: str) -> dict[str, int]:
        m = re.search(r'^TOTAL\s+(\d+)\s+(\d+)\s+(\d+)%', text, re.MULTILINE)
        if m:
            return {"stmts": int(m.group(1)), "miss": int(m.group(2)), "cover": int(m.group(3))}
        return {"stmts": 0, "miss": 0, "cover": 0}

    def _parse_test_summary(self, text: str) -> tuple:
        passed = failed = skipped = 0
        m = re.search(r'(\d+)\s+passed', text)
        if m:
            passed = int(m.group(1))
        m = re.search(r'(\d+)\s+failed', text)
        if m:
            failed = int(m.group(1))
        m = re.search(r'(\d+)\s+skipped', text)
        if m:
            skipped = int(m.group(1))
        return passed, failed, skipped

    def _parse_coverage_failure(self, text: str) -> str | None:
        """Detect --cov-fail-under threshold failures."""
        m = re.search(r'FAIL Required test coverage of \d+%', text)
        if m:
            return m.group(0)
        return None

    def _extract_summary(self, text: str) -> str:
        m = re.search(r'=+ (.+?) =+$', text.strip(), re.MULTILINE)
        if m:
            return m.group(1).strip()
        return ""

