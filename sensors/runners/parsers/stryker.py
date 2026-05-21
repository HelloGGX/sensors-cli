"""Stryker mutation-testing-report.json parser.

Parses the JSON report schema from mutation-testing-elements (schemaVersion 1.x).

Metrics match Stryker's documented definitions:
https://stryker-mutator.io/docs/mutation-testing-elements/mutant-states-and-metrics

- detected = Killed + Timeout
- valid (for \"of total\") = Killed + Timeout + Survived + NoCoverage
- covered (for \"of covered\") = Killed + Timeout + Survived (= detected + Survived)
- mutation score (of total) = detected / valid * 100
- mutation score based on covered code = detected / covered * 100

Invalid mutants (CompileError, RuntimeError), Ignored, and Pending are excluded from
valid/covered denominators, consistent with the HTML report.

Performance: one ``json.loads`` then a single pass over mutants — we do not retain
per-file ``source`` text or per-survivor details in the parsed output, only counts.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Final

from sensors.persistence.models import RunnerResult, ScoreInfo

from .base import OutputParser

# Schema: MutantStatus enum in mutation-testing-report-schema.json
_STATUS_TO_KEY: Final[dict[str, str]] = {
    "Killed": "killed",
    "Timeout": "timeout",
    "Survived": "survived",
    "NoCoverage": "noCoverage",
    "CompileError": "compileError",
    "RuntimeError": "runtimeError",
    "Ignored": "ignored",
    "Pending": "pending",
}


def _pct(numer: int, denom: int) -> float:
    if denom <= 0:
        return 100.0
    return round(100.0 * numer / denom, 2)


def _empty_count_map() -> dict[str, int]:
    return {k: 0 for k in _STATUS_TO_KEY.values()} | {"unknown": 0}


class StrykerParser(OutputParser):
    """Parser for Stryker / mutation-testing-elements JSON report files."""

    async def parse_output(self, output: str) -> RunnerResult:
        try:
            text = output.strip()
            if not text:
                return RunnerResult(
                    timestamp=datetime.now(),
                    success=False,
                    output={"parseError": "empty output", "raw": ""},
                )
            data = json.loads(text)
            if not isinstance(data, dict):
                return RunnerResult(
                    timestamp=datetime.now(),
                    success=False,
                    output={"parseError": "expected JSON object", "raw": text[:500]},
                )

            files = data.get("files")
            if not isinstance(files, dict):
                return RunnerResult(
                    timestamp=datetime.now(),
                    success=False,
                    output={"parseError": "missing or invalid 'files'", "raw": text[:500]},
                )

            cmap = _empty_count_map()

            for file_result in files.values():
                if not isinstance(file_result, dict):
                    continue
                mutants = file_result.get("mutants") or []
                if not isinstance(mutants, list):
                    continue
                for m in mutants:
                    if not isinstance(m, dict):
                        continue
                    status = m.get("status", "")
                    if not isinstance(status, str):
                        status = str(status)
                    key = _STATUS_TO_KEY.get(status, "unknown")
                    cmap[key] = cmap.get(key, 0) + 1

            killed, timeout = cmap["killed"], cmap["timeout"]
            survived, noc = cmap["survived"], cmap["noCoverage"]
            detected = killed + timeout
            valid = killed + timeout + survived + noc
            covered = killed + timeout + survived
            score_total = _pct(detected, valid)
            score_covered = _pct(detected, covered)

            thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}

            out: dict[str, Any] = {
                "schemaVersion": data.get("schemaVersion", ""),
                "counts": {
                    **cmap,
                    "detected": detected,
                    "valid": valid,
                    "covered": covered,
                },
                "mutationScoreOfTotal": score_total,
                "mutationScoreOfCovered": score_covered,
                "thresholds": thresholds,
            }
            return RunnerResult(
                timestamp=datetime.now(),
                success=True,
                output=out,
            )
        except json.JSONDecodeError as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )
        except Exception as e:
            return RunnerResult(
                timestamp=datetime.now(),
                success=False,
                output={"parseError": str(e), "raw": output[:500]},
            )

    def calculate_score(self, result: RunnerResult) -> ScoreInfo:
        """Main trend score: mutation score of covered code (0–100, higher is better)."""
        if result.output.get("parseError"):
            return ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)")
        pct = result.output.get("mutationScoreOfCovered")
        v = int(round(float(pct))) if isinstance(pct, (int, float)) else 0
        return ScoreInfo(
            value=max(0, min(100, v)),
            direction="more",
            description="Mutation score (% of covered)",
        )

    def _scores(self, result: RunnerResult) -> tuple[float, float]:
        out = result.output
        t = out.get("mutationScoreOfTotal", 0.0)
        c = out.get("mutationScoreOfCovered", 0.0)
        try:
            ft = float(t)
        except (TypeError, ValueError):
            ft = 0.0
        try:
            fc = float(c)
        except (TypeError, ValueError):
            fc = 0.0
        return ft, fc

    def _summary_line(self, result: RunnerResult) -> str:
        if result.output.get("parseError"):
            return str(result.output.get("parseError", "parse error"))
        ft, fc = self._scores(result)
        c = result.output.get("counts", {})
        return (
            f"{fc:.2f}% of covered "
            f"({ft:.2f}% of total, {c.get('survived', 0)} survived, "
            f"{c.get('detected', 0)}/{c.get('valid', 0)} valid)"
        )

    def _threshold_color_terminal(self, result: RunnerResult) -> str:
        if not result.success:
            return "red"
        ft, fc = self._scores(result)
        thresholds = result.output.get("thresholds") or {}
        high = thresholds.get("high")
        low = thresholds.get("low")
        try:
            if isinstance(high, (int, float)) and fc >= float(high):
                return "green"
            if isinstance(low, (int, float)) and fc >= float(low):
                return "yellow"
        except (TypeError, ValueError):
            pass
        return "red"

    # -- Details --

    def format_details_terminal(self, result: RunnerResult) -> str:
        if result.output.get("parseError"):
            return f"[red]{result.output.get('parseError', 'error')}[/red]"
        color = self._threshold_color_terminal(result)
        return f"[{color}]{self._summary_line(result)}[/{color}]"

    def format_details_html(self, result: RunnerResult) -> str:
        if result.output.get("parseError"):
            return f'<span class="sensors-error">{result.output.get("parseError", "error")}</span>'
        css = "sensors-success" if result.success else "sensors-error"
        if result.success:
            _, fc = self._scores(result)
            thresholds = result.output.get("thresholds") or {}
            high = thresholds.get("high")
            try:
                if isinstance(high, (int, float)) and fc < float(high):
                    css = "sensors-warn"
            except (TypeError, ValueError):
                pass
        return f'<span class="{css}">{self._summary_line(result)}</span>'

    def format_details_llm(self, result: RunnerResult) -> str:
        return self._summary_line(result)

    # -- Failures --

    def format_failures_terminal(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        if result.output.get("parseError"):
            return f"  [red]Parse error: {result.output['parseError']}[/red]"
        return self._format_failures_threshold(result, "terminal")

    def format_failures_html(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        if result.output.get("parseError"):
            return f'<span class="sensors-error">Parse error: {result.output["parseError"]}</span>'
        return self._format_failures_threshold(result, "html")

    def format_failures_llm(self, result: RunnerResult) -> str:
        if result.success:
            return ""
        if result.output.get("parseError"):
            return f"Parse error: {result.output['parseError']}"
        return self._format_failures_threshold(result, "llm")

    def _format_failures_threshold(self, result: RunnerResult, style: str) -> str:
        """Short check output: same metrics as details (scores and counts), no mutant listing."""
        line = self._summary_line(result)
        if style == "terminal":
            return f"  [red]{line}[/red]"
        if style == "html":
            return f'<span class="sensors-error">{line}</span>'
        return f"  {line}"
