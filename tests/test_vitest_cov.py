"""Tests for VitestCovParser — parses coverage/coverage-final.json (istanbul JSON format)."""

import json
from datetime import datetime

import pytest

from sensors.config import RunnerResult
from sensors.runners.parsers.vitest_cov import VitestCovParser, _format_line_ranges

# ---------------------------------------------------------------------------
# Sample data: realistic istanbul coverage-final.json structure
# ---------------------------------------------------------------------------

_DOMAIN_TS = {
    "path": "server/google/domain.ts",
    "statementMap": {
        "0": {"start": {"line": 10, "column": 0}, "end": {"line": 10, "column": 20}},
        "1": {"start": {"line": 11, "column": 0}, "end": {"line": 11, "column": 20}},
        "2": {"start": {"line": 110, "column": 0}, "end": {"line": 110, "column": 20}},
        "3": {"start": {"line": 187, "column": 0}, "end": {"line": 187, "column": 20}},
        "4": {"start": {"line": 249, "column": 0}, "end": {"line": 249, "column": 20}},
    },
    "fnMap": {
        "0": {"name": "handleRequest", "decl": {}, "loc": {}},
        "1": {"name": "parseToken", "decl": {}, "loc": {}},
        "2": {"name": "_unused", "decl": {}, "loc": {}},
    },
    "branchMap": {
        "0": {"type": "if", "locations": [{}, {}]},
        "1": {"type": "if", "locations": [{}, {}]},
    },
    # s: statements 0 and 1 hit; 2,3,4 uncovered
    "s": {"0": 5, "1": 3, "2": 0, "3": 0, "4": 0},
    # f: fn 0 and 1 hit; 2 uncovered
    "f": {"0": 5, "1": 3, "2": 0},
    # b: branch 0 both taken; branch 1 only first taken
    "b": {"0": [3, 2], "1": [1, 0]},
}

_CONFIG_TS = {
    "path": "server/config.ts",
    "statementMap": {
        "0": {"start": {"line": 1, "column": 0}, "end": {"line": 1, "column": 20}},
        "1": {"start": {"line": 2, "column": 0}, "end": {"line": 2, "column": 20}},
    },
    "fnMap": {
        "0": {"name": "getConfig", "decl": {}, "loc": {}},
    },
    "branchMap": {},
    "s": {"0": 10, "1": 8},
    "f": {"0": 10},
    "b": {},
}

_MIDDLEWARE_TS = {
    "path": "server/auth/middleware.ts",
    "statementMap": {
        "0": {"start": {"line": 5, "column": 0}, "end": {"line": 5, "column": 20}},
    },
    "fnMap": {
        "0": {"name": "authMiddleware", "decl": {}, "loc": {}},
    },
    "branchMap": {
        "0": {"type": "if", "locations": [{}, {}]},
    },
    "s": {"0": 7},
    "f": {"0": 7},
    "b": {"0": [7, 5]},
}

SAMPLE_JSON = json.dumps({
    "server/google/domain.ts": _DOMAIN_TS,
    "server/config.ts": _CONFIG_TS,
    "server/auth/middleware.ts": _MIDDLEWARE_TS,
})

SAMPLE_JSON_EMPTY = json.dumps({})


def _make_result(success, **output_fields) -> RunnerResult:
    return RunnerResult(timestamp=datetime.utcnow(), success=success, output=output_fields)


# ---------------------------------------------------------------------------
# parse_output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_output_success():
    parser = VitestCovParser()
    result = await parser.parse_output(SAMPLE_JSON)

    assert result.success is True
    assert result.output["totalStmts"] > 0
    assert result.output["totalBranch"] > 0
    assert result.output["totalFuncs"] > 0
    assert len(result.output["files"]) == 3


@pytest.mark.asyncio
async def test_parse_output_per_file_coverage():
    parser = VitestCovParser()
    result = await parser.parse_output(SAMPLE_JSON)
    files = result.output["files"]

    domain = next(f for f in files if f["name"] == "domain.ts")
    # 2 of 5 statements hit → 40%
    assert domain["stmts"] == 40.0
    # 2 of 3 functions hit → 66.67%
    assert domain["funcs"] == pytest.approx(66.67, abs=0.1)
    # branches: 0→[3,2] both hit; 1→[1,0] one hit → 3 of 4 → 75%
    assert domain["branch"] == 75.0
    # uncovered lines from statements 2,3,4 → lines 110,187,249
    assert domain["uncovered"] == "110,187,249"

    config = next(f for f in files if f["name"] == "config.ts")
    assert config["stmts"] == 100.0
    assert config["funcs"] == 100.0
    assert config["uncovered"] == ""


@pytest.mark.asyncio
async def test_parse_output_aggregate_totals():
    parser = VitestCovParser()
    result = await parser.parse_output(SAMPLE_JSON)

    # Aggregate stmts: 2+2+1 hit out of 5+2+1 = 5/8 = 62.5%
    assert result.output["totalStmts"] == pytest.approx(62.5)
    # Aggregate funcs: 2+1+1 hit out of 3+1+1 = 4/5 = 80%
    assert result.output["totalFuncs"] == 80.0
    # Aggregate branches: (3+2+1)=6 hit out of (2+2+0+2)... let me recount
    # domain.b: {"0":[3,2],"1":[1,0]} → 3 hit of 4 total
    # config.b: {} → 0/0
    # middleware.b: {"0":[7,5]} → 2 hit of 2
    # Total: 3+0+2=5 hit of 4+0+2=6 → 83.33%
    assert result.output["totalBranch"] == pytest.approx(83.33, abs=0.1)


@pytest.mark.asyncio
async def test_parse_output_empty_json():
    parser = VitestCovParser()
    result = await parser.parse_output(SAMPLE_JSON_EMPTY)

    assert result.success is True
    assert result.output["totalStmts"] == 100.0  # 0/0 → 100%
    assert result.output["files"] == []


@pytest.mark.asyncio
async def test_parse_output_invalid_json():
    parser = VitestCovParser()
    result = await parser.parse_output("not json at all")

    assert result.success is False
    assert "parseError" in result.output


@pytest.mark.asyncio
async def test_parse_output_garbage_json():
    parser = VitestCovParser()
    result = await parser.parse_output("{}")

    assert result is not None
    assert result.success is True
    assert result.output["files"] == []


# ---------------------------------------------------------------------------
# is_watch_run_complete
# ---------------------------------------------------------------------------

def test_watch_complete_passed():
    parser = VitestCovParser()
    assert parser.is_watch_run_complete("      Tests  38 passed (38)") is True


def test_watch_complete_failed():
    parser = VitestCovParser()
    assert parser.is_watch_run_complete("      Tests  2 failed | 36 passed (38)") is True


def test_watch_not_complete():
    parser = VitestCovParser()
    assert parser.is_watch_run_complete("running tests...") is False


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

def test_calculate_score():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=85.5)
    score = parser.calculate_score(result)

    assert score.value == 85
    assert score.direction == "more"


# ---------------------------------------------------------------------------
# format_details
# ---------------------------------------------------------------------------

def test_format_details_terminal_high():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=95.0)
    out = parser.format_details_terminal(result)
    assert "[green]" in out
    assert "95.0% branch" in out


def test_format_details_terminal_medium():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=60.0)
    out = parser.format_details_terminal(result)
    assert "[yellow]" in out


def test_format_details_terminal_low():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=30.0)
    out = parser.format_details_terminal(result)
    assert "[red]" in out


def test_format_details_terminal_failure():
    parser = VitestCovParser()
    result = _make_result(False, totalBranch=90.0)
    out = parser.format_details_terminal(result)
    assert "[red]" in out


def test_format_details_html_success():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=90.0)
    out = parser.format_details_html(result)
    assert "sensors-success" in out
    assert "90.0% branch" in out


def test_format_details_html_warn():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=55.0)
    out = parser.format_details_html(result)
    assert "sensors-warn" in out


def test_format_details_llm():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=97.9)
    out = parser.format_details_llm(result)
    assert out == "97.9% branch"


# ---------------------------------------------------------------------------
# format_failures
# ---------------------------------------------------------------------------

def test_format_failures_success_returns_empty():
    parser = VitestCovParser()
    result = _make_result(True, totalBranch=90, files=[])
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_with_uncovered_lines():
    parser = VitestCovParser()
    result = _make_result(
        False,
        totalBranch=60.0,
        files=[
            {"name": "domain.ts", "stmts": 75, "branch": 60, "funcs": 80, "lines": 72.5,
             "uncovered": "10-30,50,99"},
        ],
    )

    terminal = parser.format_failures_terminal(result)
    assert "domain.ts" in terminal
    assert "10-30,50,99" in terminal

    html = parser.format_failures_html(result)
    assert "sensors-file" in html
    assert "domain.ts" in html

    llm = parser.format_failures_llm(result)
    assert "domain.ts" in llm


def test_format_failures_no_uncovered():
    parser = VitestCovParser()
    result = _make_result(
        False,
        totalBranch=100,
        files=[
            {"name": "config.ts", "stmts": 100, "branch": 100, "funcs": 100, "lines": 100,
             "uncovered": ""},
        ],
    )
    llm = parser.format_failures_llm(result)
    assert "config.ts" not in llm


def test_format_failures_parse_error():
    parser = VitestCovParser()
    result = _make_result(False, parseError="unexpected token")
    terminal = parser.format_failures_terminal(result)
    assert "Parse error" in terminal


def test_format_details_parse_error_shows_message():
    parser = VitestCovParser()
    result = _make_result(False, parseError="configure result")
    assert "configure result" in parser.format_details_terminal(result)
    assert "configure result" in parser.format_details_html(result)
    assert "configure result" in parser.format_details_llm(result)


@pytest.mark.asyncio
async def test_parse_output_non_json_gives_helpful_error():
    parser = VitestCovParser()
    result = await parser.parse_output("vitest stdout text coverage table...")
    assert result.success is False
    assert "result" in result.output["parseError"]
    assert "coverage-final.json" in result.output["parseError"]


# ---------------------------------------------------------------------------
# _format_line_ranges helper
# ---------------------------------------------------------------------------

def test_format_line_ranges_empty():
    assert _format_line_ranges([]) == ""


def test_format_line_ranges_single():
    assert _format_line_ranges([5]) == "5"


def test_format_line_ranges_consecutive():
    assert _format_line_ranges([10, 11, 12]) == "10-12"


def test_format_line_ranges_mixed():
    assert _format_line_ranges([10, 11, 12, 15, 20, 21]) == "10-12,15,20-21"


def test_format_line_ranges_scattered():
    assert _format_line_ranges([1, 3, 5]) == "1,3,5"


# ---------------------------------------------------------------------------
# integration
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_integration_full_pipeline():
    parser = VitestCovParser()
    result = await parser.parse_output(SAMPLE_JSON)

    assert result.success is True

    assert parser.format_details_terminal(result)
    assert parser.format_details_html(result)
    assert parser.format_details_llm(result)

    # Success → no failures output
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""

    score = parser.calculate_score(result)
    assert score.value >= 0
    assert score.direction == "more"
