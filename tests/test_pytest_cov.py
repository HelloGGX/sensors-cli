from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers.pytest_cov import PytestCovParser

SAMPLE_OUTPUT = """\
===================================== tests coverage ======================================
_____________________ coverage: platform linux, python 3.11.2-final-0 _____________________

Name                                    Stmts   Miss  Cover   Missing
---------------------------------------------------------------------
sensors/__init__.py                         1      0   100%
sensors/cli.py                             17     17     0%   3-35
sensors/config/__init__.py                  3      0   100%
sensors/config/loader.py                   58     48    17%   32-70, 86-124
sensors/display.py                        157     86    45%   48-55, 65-70
sensors/parsers/base.py                    29      9    69%   46, 65, 81
---------------------------------------------------------------------
TOTAL                                     265    160    40%
======================= 55 passed, 2 skipped, 11 warnings in 2.66s ========================\
"""

SAMPLE_OUTPUT_FAIL = """\
===================================== tests coverage ======================================
_____________________ coverage: platform linux, python 3.11.2-final-0 _____________________

Name              Stmts   Miss  Cover   Missing
------------------------------------------------
app/main.py          50     10    80%   12-21
------------------------------------------------
TOTAL                50     10    80%

FAIL Required test coverage of 90%. Got 80%
========================== 5 passed in 1.00s ==========================\
"""


def _make_result(success, **output_fields) -> RunnerResult:
    return RunnerResult(timestamp=datetime.utcnow(), success=success, output=output_fields)


# -- parse_output --

@pytest.mark.asyncio
async def test_parse_output_success():
    parser = PytestCovParser()
    result = await parser.parse_output(SAMPLE_OUTPUT)

    assert result.success is True
    assert result.output["totalCoverage"] == 40
    assert result.output["totalStatements"] == 265
    assert result.output["totalMisses"] == 160
    assert result.output["numPassedTests"] == 55
    assert result.output["numSkipped"] == 2
    assert result.output["numFailedTests"] == 0
    assert result.output["coverageFailure"] is None
    assert len(result.output["files"]) == 6


@pytest.mark.asyncio
async def test_parse_output_file_details():
    parser = PytestCovParser()
    result = await parser.parse_output(SAMPLE_OUTPUT)
    files = result.output["files"]

    init = next(f for f in files if f["name"] == "sensors/__init__.py")
    assert init["stmts"] == 1
    assert init["miss"] == 0
    assert init["cover"] == 100

    cli = next(f for f in files if f["name"] == "sensors/cli.py")
    assert cli["cover"] == 0
    assert cli["missing"] == "3-35"


@pytest.mark.asyncio
async def test_parse_output_cov_fail_under():
    parser = PytestCovParser()
    result = await parser.parse_output(SAMPLE_OUTPUT_FAIL)

    assert result.success is False
    assert result.output["totalCoverage"] == 80
    assert result.output["coverageFailure"] is not None
    assert "90%" in result.output["coverageFailure"]


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = PytestCovParser()
    result = await parser.parse_output("")

    assert result.success is True
    assert result.output["totalCoverage"] == 0
    assert result.output["files"] == []


@pytest.mark.asyncio
async def test_parse_output_garbage():
    parser = PytestCovParser()
    result = await parser.parse_output("not a coverage report at all")

    assert result is not None
    assert result.output["totalCoverage"] == 0


# -- calculate_score --

def test_calculate_score():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=75)
    score = parser.calculate_score(result)

    assert score.value == 75
    assert score.direction == "more"


# -- format_details --

def test_format_details_terminal_high_coverage():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=85, numPassedTests=10, numFailedTests=0)
    out = parser.format_details_terminal(result)
    assert "[green]" in out
    assert "85% coverage" in out


def test_format_details_terminal_medium_coverage():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=60, numPassedTests=10, numFailedTests=0)
    out = parser.format_details_terminal(result)
    assert "[yellow]" in out


def test_format_details_terminal_low_coverage():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=20, numPassedTests=10, numFailedTests=0)
    out = parser.format_details_terminal(result)
    assert "[red]" in out


def test_format_details_terminal_failure():
    parser = PytestCovParser()
    result = _make_result(False, totalCoverage=80, numPassedTests=4, numFailedTests=1)
    out = parser.format_details_terminal(result)
    assert "[red]" in out
    assert "1 failed" in out


def test_format_details_html_success():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=90, numPassedTests=10, numFailedTests=0)
    out = parser.format_details_html(result)
    assert "sensors-success" in out
    assert "90% coverage" in out


def test_format_details_html_warn():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=55, numPassedTests=10, numFailedTests=0)
    out = parser.format_details_html(result)
    assert "sensors-warn" in out


def test_format_details_llm():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=50, numPassedTests=55, numFailedTests=0)
    out = parser.format_details_llm(result)
    assert out == "50% coverage, 55 passed"


# -- format_failures --

def test_format_failures_success_returns_empty():
    parser = PytestCovParser()
    result = _make_result(True, totalCoverage=80, files=[], numFailedTests=0)
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_cov_fail():
    parser = PytestCovParser()
    result = _make_result(
        False,
        totalCoverage=80,
        numFailedTests=0,
        coverageFailure="FAIL Required test coverage of 90%",
        files=[{"name": "app/main.py", "stmts": 50, "miss": 10, "cover": 80, "missing": ""}],
    )
    out = parser.format_failures_llm(result)
    assert "FAIL Required test coverage" in out


def test_format_failures_low_coverage_files():
    parser = PytestCovParser()
    result = _make_result(
        False,
        totalCoverage=40,
        numFailedTests=1,
        coverageFailure=None,
        files=[
            {"name": "a.py", "stmts": 10, "miss": 10, "cover": 0, "missing": "1-10"},
            {"name": "b.py", "stmts": 10, "miss": 8, "cover": 20, "missing": "1-8"},
            {"name": "c.py", "stmts": 10, "miss": 0, "cover": 100, "missing": ""},
        ],
    )

    terminal = parser.format_failures_terminal(result)
    assert "Low coverage" in terminal
    assert "a.py" in terminal
    assert "b.py" in terminal
    assert "c.py" not in terminal

    html = parser.format_failures_html(result)
    assert "sensors-file" in html
    assert "a.py" in html

    llm = parser.format_failures_llm(result)
    assert "a.py" in llm
    assert "0%" in llm


def test_format_failures_html_cov_fail():
    parser = PytestCovParser()
    result = _make_result(
        False,
        totalCoverage=70,
        numFailedTests=0,
        coverageFailure="FAIL Required test coverage of 80%",
        files=[],
    )
    out = parser.format_failures_html(result)
    assert "sensors-error" in out
    assert "FAIL Required" in out
