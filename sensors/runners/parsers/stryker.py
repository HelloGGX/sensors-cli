"""Stryker mutation-testing-report.json parser.

Parses the JSON report schema from mutation-testing-elements (schemaVersion 1.x).

Metrics match Stryker's documented definitions:
https://stryker-mutator.io/docs/mutation-testing-elements/mutant-states-and-metrics

- detected = Killed + Timeout
- valid (for "of total") = Killed + Timeout + Survived + NoCoverage
- covered (for "of covered") = Killed + Timeout + Survived (= detected + Survived)
- mutation score (of total) = detected / valid * 100
- mutation score based on covered code = detected / covered * 100

Invalid mutants (CompileError, RuntimeError), Ignored, and Pending are excluded from
valid/covered denominators, consistent with the HTML report.

Performance: one ``json.loads`` then a single pass over mutants — we do not retain
per-file ``source`` text or per-survivor details in the parsed output, only counts.
"""

from __future__ import annotations

import json
from typing import Any, Final

from sensors.config import Metric, ParsedOutput, ScoreInfo

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


def _count_mutant_statuses(files: dict[str, Any]) -> dict[str, int]:
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
    return cmap


class StrykerParser(OutputParser):
    """Parser for Stryker / mutation-testing-elements JSON report files."""

    def parse(self, output: str) -> ParsedOutput:
        try:
            text = output.strip()
            if not text:
                return ParsedOutput(
                    success=False,
                    summary="Parse error: empty output",
                    score=ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)"),
                    extra={"parseError": "empty output"},
                )

            data = json.loads(text)
            if not isinstance(data, dict):
                return ParsedOutput(
                    success=False,
                    summary="Parse error: expected JSON object",
                    score=ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)"),
                    extra={"parseError": "expected JSON object"},
                )
            files = data.get("files")
            if not isinstance(files, dict):
                return ParsedOutput(
                    success=False,
                    summary="Parse error: missing or invalid 'files'",
                    score=ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)"),
                    extra={"parseError": "missing or invalid 'files'"},
                )

            cmap = _count_mutant_statuses(files)
            killed, timeout = cmap["killed"], cmap["timeout"]
            survived, noc = cmap["survived"], cmap["noCoverage"]
            detected = killed + timeout
            valid = killed + timeout + survived + noc
            covered = killed + timeout + survived

            score_of_total = _pct(detected, valid)
            score_of_covered = _pct(detected, covered)

            thresholds = data.get("thresholds") if isinstance(data.get("thresholds"), dict) else {}
            high = thresholds.get("high") if thresholds else None
            threshold_val = float(high) if isinstance(high, (int, float)) else None

            summary = (
                f"{score_of_covered:.2f}% of covered "
                f"({score_of_total:.2f}% of total, {survived} survived, "
                f"{detected}/{valid} valid)"
            )

            return ParsedOutput(
                success=True,
                summary=summary,
                score=ScoreInfo(
                    value=max(0, min(100, int(round(score_of_covered)))),
                    direction="more",
                    description="Mutation score (% of covered)",
                ),
                metrics=[
                    Metric(
                        "mutationScoreOfCovered",
                        "Mutation score (covered)",
                        score_of_covered,
                        unit="%",
                        direction="more",
                        threshold=threshold_val,
                    ),
                    Metric(
                        "mutationScoreOfTotal",
                        "Mutation score (total)",
                        score_of_total,
                        unit="%",
                        direction="more",
                    ),
                    Metric("killed", "Killed", killed),
                    Metric("survived", "Survived", survived),
                    Metric("noCoverage", "No coverage", noc),
                    Metric("detected", "Detected", detected),
                    Metric("valid", "Valid", valid),
                    Metric("covered", "Covered", covered),
                ],
            )
        except json.JSONDecodeError as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)"),
                extra={"parseError": str(e)},
            )
        except Exception as e:
            return ParsedOutput(
                success=False,
                summary=f"Parse error: {e}",
                score=ScoreInfo(value=0, direction="more", description="Mutation score (% of covered)"),
                extra={"parseError": str(e)},
            )
