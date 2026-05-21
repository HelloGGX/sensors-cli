"""Tests for StrykerParser."""

import json
from pathlib import Path

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers.stryker import StrykerParser, _pct


def _loc():
    return {"start": {"line": 1, "column": 1}, "end": {"line": 1, "column": 2}}


def _minimal_report(mutants: list[dict], thresholds: dict | None = None) -> str:
    th = thresholds if thresholds is not None else {"high": 80, "low": 60, "break": None}
    return json.dumps({
        "schemaVersion": "1.0",
        "thresholds": th,
        "files": {
            "src/a.ts": {
                "language": "typescript",
                "source": "x",
                "mutants": mutants,
            },
        },
    })


@pytest.mark.asyncio
async def test_parse_all_killed_perfect_scores():
    mutants = [{"id": str(i), "mutatorName": "M", "location": _loc(), "status": "Killed"} for i in range(5)]
    parser = StrykerParser()
    result = await parser.parse_output(_minimal_report(mutants))
    assert result.success
    assert result.output["mutationScoreOfTotal"] == 100.0
    assert result.output["mutationScoreOfCovered"] == 100.0
    assert result.output["counts"]["valid"] == 5
    assert parser.calculate_score(result).value == 100
    assert "100.00%" in parser.format_details_llm(result)
    assert parser.format_failures_llm(result) == ""


@pytest.mark.asyncio
async def test_metrics_match_stryker_docs():
    """detected=3, valid=5, covered=4 -> 60% total, 75% covered. Ignored excluded from valid."""
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Killed"},
        {"id": "3", "mutatorName": "C", "location": _loc(), "status": "Killed"},
        {"id": "4", "mutatorName": "D", "location": _loc(), "status": "Survived"},
        {"id": "5", "mutatorName": "E", "location": _loc(), "status": "NoCoverage"},
    ]
    parser = StrykerParser()
    result = await parser.parse_output(_minimal_report(mutants))
    assert result.output["mutationScoreOfTotal"] == 60.0
    assert result.output["mutationScoreOfCovered"] == 75.0
    assert result.success
    assert parser.calculate_score(result).value == 75  # leading score is "of covered"


@pytest.mark.asyncio
async def test_ignored_not_in_valid_denominator():
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Ignored"},
    ]
    parser = StrykerParser()
    result = await parser.parse_output(_minimal_report(mutants))
    assert result.output["counts"]["valid"] == 1
    assert result.output["mutationScoreOfTotal"] == 100.0


@pytest.mark.asyncio
async def test_below_threshold_still_succeeds():
    """Score below Stryker's embedded threshold still returns success=True.
    The below_threshold status is handled at the runner level via sensors config."""
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Survived"},
    ]
    parser = StrykerParser()
    result = await parser.parse_output(_minimal_report(mutants))
    # detected=1, valid=2, covered=2 -> 50% / 50% — below low threshold of 60
    assert result.output["mutationScoreOfCovered"] == 50.0
    assert result.success  # process ran cleanly; threshold handled by runner
    assert parser.format_failures_llm(result) == ""


@pytest.mark.asyncio
async def test_parse_error_formatters():
    parser = StrykerParser()
    result = await parser.parse_output("not json")
    assert not result.success
    assert "parse" in parser.format_details_llm(result).lower() or "Expecting" in parser.format_details_llm(result)
    assert parser.format_failures_llm(result)


@pytest.mark.asyncio
async def test_sample_stryker_json_file():
    root = Path(__file__).resolve().parents[2]
    path = root / "sample_stryker.json"
    if not path.is_file():
        pytest.skip("sample_stryker.json not at repo root")
    text = path.read_text(encoding="utf-8")
    parser = StrykerParser()
    result = await parser.parse_output(text)
    assert result.success
    assert result.output["mutationScoreOfCovered"] == 100.0
    assert result.output["counts"]["killed"] == 5


@pytest.mark.asyncio
async def test_failure_output_short_no_mutant_paths():
    mutants = [
        {
            "id": str(i),
            "mutatorName": "StringLiteral",
            "location": {"start": {"line": i, "column": 1}, "end": {"line": i, "column": 2}},
            "status": "Survived",
        }
        for i in range(3)
    ]
    parser = StrykerParser()
    result = await parser.parse_output(_minimal_report(mutants))
    assert "survivedDetails" not in result.output
    assert result.success
    assert parser.format_failures_terminal(result) == ""


def test_pct_edge_cases():
    assert _pct(0, 0) == 100.0
    assert _pct(3, 5) == 60.0


@pytest.mark.asyncio
async def test_calculate_score_parse_error():
    parser = StrykerParser()
    r = RunnerResult(
        timestamp=__import__("datetime").datetime.now(),
        success=False,
        output={"parseError": "bad"},
    )
    assert parser.calculate_score(r).value == 0
