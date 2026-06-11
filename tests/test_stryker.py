"""Tests for StrykerParser."""

import json
from pathlib import Path

import pytest

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


def _metric(parsed, key):
    return next(m for m in parsed.metrics if m.key == key)


def test_parse_all_killed_perfect_scores():
    mutants = [{"id": str(i), "mutatorName": "M", "location": _loc(), "status": "Killed"} for i in range(5)]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants))

    assert parsed.success is True
    assert _metric(parsed, "mutationScoreOfTotal").value == 100.0
    assert _metric(parsed, "mutationScoreOfCovered").value == 100.0
    assert _metric(parsed, "valid").value == 5
    assert parsed.score.value == 100
    assert "100.00%" in parsed.summary


def test_metrics_match_stryker_docs():
    """detected=3, valid=5, covered=4 -> 60% total, 75% covered. Ignored excluded from valid."""
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Killed"},
        {"id": "3", "mutatorName": "C", "location": _loc(), "status": "Killed"},
        {"id": "4", "mutatorName": "D", "location": _loc(), "status": "Survived"},
        {"id": "5", "mutatorName": "E", "location": _loc(), "status": "NoCoverage"},
    ]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants))

    assert _metric(parsed, "mutationScoreOfTotal").value == 60.0
    assert _metric(parsed, "mutationScoreOfCovered").value == 75.0
    assert parsed.success is True
    assert parsed.score.value == 75  # leading score is "of covered"


def test_ignored_not_in_valid_denominator():
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Ignored"},
    ]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants))

    assert _metric(parsed, "valid").value == 1
    assert _metric(parsed, "mutationScoreOfTotal").value == 100.0


def test_below_threshold_still_succeeds():
    """Score below Stryker's embedded threshold still returns success=True.
    The below_threshold status is handled at the runner level via sensors config."""
    mutants = [
        {"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"},
        {"id": "2", "mutatorName": "B", "location": _loc(), "status": "Survived"},
    ]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants))

    # detected=1, valid=2, covered=2 -> 50% / 50% — below low threshold of 60
    assert _metric(parsed, "mutationScoreOfCovered").value == 50.0
    assert parsed.success is True  # process ran cleanly; threshold handled by runner


def test_parse_error():
    parser = StrykerParser()
    parsed = parser.parse("not json")

    assert parsed.success is False
    assert "parseError" in parsed.extra
    assert "parse" in parsed.summary.lower() or "Expecting" in parsed.summary


def test_sample_stryker_json_file():
    root = Path(__file__).resolve().parents[2]
    path = root / "sample_stryker.json"
    if not path.is_file():
        pytest.skip("sample_stryker.json not at repo root")
    text = path.read_text(encoding="utf-8")
    parser = StrykerParser()
    parsed = parser.parse(text)

    assert parsed.success is True
    assert _metric(parsed, "mutationScoreOfCovered").value == 100.0
    assert _metric(parsed, "killed").value == 5


def test_no_findings_in_output():
    """Stryker parser never produces findings — metrics only."""
    mutants = [
        {"id": str(i), "mutatorName": "StringLiteral", "location": _loc(), "status": "Survived"}
        for i in range(3)
    ]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants))

    assert parsed.findings == []
    assert parsed.success is True


def test_threshold_metric_set_from_report():
    """mutationScoreOfCovered metric carries threshold from the report's high value."""
    mutants = [{"id": "1", "mutatorName": "A", "location": _loc(), "status": "Killed"}]
    parser = StrykerParser()
    parsed = parser.parse(_minimal_report(mutants, thresholds={"high": 90, "low": 70, "break": None}))

    m = _metric(parsed, "mutationScoreOfCovered")
    assert m.threshold == 90.0


def test_pct_edge_cases():
    assert _pct(0, 0) == 100.0
    assert _pct(3, 5) == 60.0
