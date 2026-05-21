"""Pytest-cov coverage output parser."""

import re
from datetime import datetime
from typing import Any

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser


class PytestCovParser(OutputParser):
    """Parser for pytest-cov coverage report output."""

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            text = output.strip()
            files = self._parse_coverage_table(text)
            total = self._parse_total(text)
            num_passed, num_failed, num_skipped = self._parse_test_summary(text)
            cov_fail = self._parse_coverage_failure(text)

            success = num_failed == 0 and cov_fail is None

            return RunnerResult(
                timestamp=datetime.now(),
                success=success,
                output={
                    "totalCoverage": total.get("cover", 0),
                    "totalStatements": total.get("stmts", 0),
                    "totalMisses": total.get("miss", 0),
                    "numPassedTests": num_passed,
                    "numFailedTests": num_failed,
                    "numSkipped": num_skipped,
                    "files": files,
                    "coverageFailure": cov_fail,
                    "summary": self._extract_summary(text),
                },
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
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

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        return ScoreInfo(
            value=result.output.get("totalCoverage", 0),
            direction="more",
            description="Test coverage percentage",
        )

    # -- Helpers --

    def _detail_text(self, result: RunnerResult) -> str:
        cov = result.output.get("totalCoverage", 0)
        passed = result.output.get("numPassedTests", 0)
        failed = result.output.get("numFailedTests", 0)
        parts = [f"{cov}% coverage"]
        if failed:
            parts.append(f"{failed} failed")
        parts.append(f"{passed} passed")
        return ", ".join(parts)

    # -- Details --

    def format_details_terminal(self, result: RunnerResult) -> str:
        text = self._detail_text(result)
        if not result.success:
            return f"[red]{text}[/red]"
        cov = result.output.get("totalCoverage", 0)
        if cov >= 80:
            return f"[green]{text}[/green]"
        if cov >= 50:
            return f"[yellow]{text}[/yellow]"
        return f"[red]{text}[/red]"

    def format_details_html(self, result: RunnerResult) -> str:
        text = self._detail_text(result)
        if not result.success:
            return f'<span class="sensors-error">{text}</span>'
        cov = result.output.get("totalCoverage", 0)
        if cov >= 80:
            return f'<span class="sensors-success">{text}</span>'
        if cov >= 50:
            return f'<span class="sensors-warn">{text}</span>'
        return f'<span class="sensors-error">{text}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._detail_text(result)

    # -- Failures --

    def _get_low_coverage_files(self, result: RunnerResult, threshold: int = 30) -> list[dict]:
        return [f for f in result.output.get("files", []) if f["cover"] < threshold]

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "terminal")

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "html")

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        return self._format_failure_items(result, "llm")

    def _format_failure_items(self, result: RunnerResult, style: str) -> str:
        lines = []

        cov_fail = result.output.get("coverageFailure")
        if cov_fail:
            if style == "terminal":
                lines.append(f"  [red]{cov_fail}[/red]")
            elif style == "html":
                lines.append(f'<div class="sensors-violation"><span class="sensors-error">{cov_fail}</span></div>')
            else:
                lines.append(f"  {cov_fail}")

        low = self._get_low_coverage_files(result)
        if low:
            if style == "terminal":
                lines.append("  [yellow]Low coverage files:[/yellow]")
                for f in low:
                    lines.append(f"    [dim]{f['name']}[/dim] {f['cover']}%")
            elif style == "html":
                for f in low:
                    lines.append(
                        f'<div class="sensors-violation">'
                        f'<span class="sensors-file">{f["name"]}</span> '
                        f'<span class="sensors-warn">{f["cover"]}%</span>'
                        f'</div>'
                    )
            else:
                lines.append("  Low coverage files:")
                for f in low:
                    lines.append(f"    {f['name']} {f['cover']}%")

        failed = result.output.get("numFailedTests", 0)
        if failed and not lines:
            msg = f"{failed} test{'s' if failed != 1 else ''} failed"
            if style == "terminal":
                lines.append(f"  [red]{msg}[/red]")
            elif style == "html":
                lines.append(f'<span class="sensors-error">{msg}</span>')
            else:
                lines.append(f"  {msg}")

        return "\n".join(lines)
