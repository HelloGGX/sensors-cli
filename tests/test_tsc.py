"""Tests for TscParser — parse_output, calculate_score, and all format methods."""

from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers.tsc import TscParser

# ---------------------------------------------------------------------------
# Sample outputs
# ---------------------------------------------------------------------------

TSC_MULTI_ERROR_OUTPUT = """\

> rest-express@1.0.0 typecheck
> tsc

server/services/chat-analytics.ts:91:26 - error TS2802: Type 'Set<string>' can only be iterated through when using the '--downlevelIteration' flag or with a '--target' of 'es2015' or higher.

91     const userKeys = [...new Set(members.map((m) => m.userKey))];
                            ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

server/services/mock-repository.ts:92:5 - error TS2739: Type '{ a: number; }' is missing the following properties from type '{ a: number; b: string; }': b

92     return {
       ~~~~~~


Found 2 errors in 2 files.

Errors  Files
     1  server/services/chat-analytics.ts:91
     1  server/services/mock-repository.ts:92
"""

TSC_SINGLE_FILE_OUTPUT = """\
src/index.ts:10:3 - error TS2322: Type 'string' is not assignable to type 'number'.

10   const x: number = "hello";
     ~

Found 1 error.
"""

TSC_CLEAN_OUTPUT = ""

TSC_WATCH_COMPLETE_LINE = "[12:00:01 AM] Found 2 errors. Watching for file changes."
TSC_WATCH_CLEAN_LINE = "[12:00:01 AM] Found 0 errors. Watching for file changes."

TSC_PAREN_FORMAT_OUTPUT = """\
src/foo.ts(5,10): error TS1005: ';' expected.

Found 1 error.
"""


def _result(*, errors=0, file_count=0, error_list=None, success=None):
    if success is None:
        success = errors == 0
    return RunnerResult(
        timestamp=datetime.utcnow(),
        success=success,
        output={
            "errorCount": errors,
            "fileCount": file_count,
            "errors": error_list or [],
        },
    )


# ---------------------------------------------------------------------------
# parse_output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_multi_error():
    parser = TscParser()
    result = await parser.parse_output(TSC_MULTI_ERROR_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 2
    assert result.output["fileCount"] == 2
    assert len(result.output["errors"]) == 2


@pytest.mark.asyncio
async def test_parse_multi_error_fields():
    parser = TscParser()
    result = await parser.parse_output(TSC_MULTI_ERROR_OUTPUT)

    e0 = result.output["errors"][0]
    assert e0["file"] == "server/services/chat-analytics.ts"
    assert e0["line"] == 91
    assert e0["column"] == 26
    assert e0["code"] == "TS2802"
    assert "downlevelIteration" in e0["message"]

    e1 = result.output["errors"][1]
    assert e1["file"] == "server/services/mock-repository.ts"
    assert e1["code"] == "TS2739"


@pytest.mark.asyncio
async def test_parse_single_file_error():
    parser = TscParser()
    result = await parser.parse_output(TSC_SINGLE_FILE_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 1
    assert result.output["fileCount"] == 1
    assert len(result.output["errors"]) == 1
    assert result.output["errors"][0]["code"] == "TS2322"


@pytest.mark.asyncio
async def test_parse_clean_output():
    parser = TscParser()
    result = await parser.parse_output(TSC_CLEAN_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["errors"] == []


@pytest.mark.asyncio
async def test_parse_paren_format():
    """tsc can emit file(line,col) format depending on config."""
    parser = TscParser()
    result = await parser.parse_output(TSC_PAREN_FORMAT_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 1
    e = result.output["errors"][0]
    assert e["file"] == "src/foo.ts"
    assert e["line"] == 5
    assert e["column"] == 10
    assert e["code"] == "TS1005"


# ---------------------------------------------------------------------------
# is_watch_run_complete
# ---------------------------------------------------------------------------

def test_watch_complete_with_errors():
    parser = TscParser()
    assert parser.is_watch_run_complete(TSC_WATCH_COMPLETE_LINE) is True


def test_watch_complete_clean():
    parser = TscParser()
    assert parser.is_watch_run_complete(TSC_WATCH_CLEAN_LINE) is True


def test_watch_not_complete():
    parser = TscParser()
    assert parser.is_watch_run_complete("error TS2322: something") is False


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

def test_calculate_score_errors():
    parser = TscParser()
    score = parser.calculate_score(_result(errors=5))
    assert score.value == 5
    assert score.direction == "less"


def test_calculate_score_clean():
    parser = TscParser()
    score = parser.calculate_score(_result())
    assert score.value == 0


# ---------------------------------------------------------------------------
# format_details_terminal
# ---------------------------------------------------------------------------

def test_format_details_terminal_errors():
    parser = TscParser()
    text = parser.format_details_terminal(_result(errors=2, file_count=2))
    assert "[red]" in text
    assert "2 errors in 2 files" in text


def test_format_details_terminal_clean():
    parser = TscParser()
    text = parser.format_details_terminal(_result())
    assert "[green]" in text
    assert "No errors" in text


def test_format_details_terminal_single_error():
    parser = TscParser()
    text = parser.format_details_terminal(_result(errors=1, file_count=1))
    assert "1 error" in text
    assert "errors" not in text


# ---------------------------------------------------------------------------
# format_details_html
# ---------------------------------------------------------------------------

def test_format_details_html_errors():
    parser = TscParser()
    html = parser.format_details_html(_result(errors=3, file_count=2))
    assert "sensors-error" in html
    assert "3 errors" in html


def test_format_details_html_clean():
    parser = TscParser()
    html = parser.format_details_html(_result())
    assert "sensors-success" in html


# ---------------------------------------------------------------------------
# format_details_llm
# ---------------------------------------------------------------------------

def test_format_details_llm_errors():
    parser = TscParser()
    assert "2 errors" in parser.format_details_llm(_result(errors=2, file_count=2))


def test_format_details_llm_clean():
    parser = TscParser()
    assert parser.format_details_llm(_result()) == "No errors"


# ---------------------------------------------------------------------------
# format_failures_terminal
# ---------------------------------------------------------------------------

def test_format_failures_terminal_success_empty():
    parser = TscParser()
    assert parser.format_failures_terminal(_result(success=True)) == ""


def test_format_failures_terminal_with_errors():
    parser = TscParser()
    result = _result(errors=1, error_list=[{
        "file": "src/index.ts", "line": 10, "column": 3,
        "code": "TS2322", "message": "Type 'string' is not assignable to type 'number'.",
    }])
    text = parser.format_failures_terminal(result)
    assert "src/index.ts:10:3" in text
    assert "TS2322" in text
    assert "[red]" in text
    assert "[dim]" in text


def test_format_failures_terminal_no_details_fallback():
    parser = TscParser()
    result = _result(errors=3)
    text = parser.format_failures_terminal(result)
    assert "no details available" in text


# ---------------------------------------------------------------------------
# format_failures_html
# ---------------------------------------------------------------------------

def test_format_failures_html_success_empty():
    parser = TscParser()
    assert parser.format_failures_html(_result(success=True)) == ""


def test_format_failures_html_with_errors():
    parser = TscParser()
    result = _result(errors=1, error_list=[{
        "file": "src/index.ts", "line": 10, "column": 3,
        "code": "TS2322", "message": "Type mismatch",
    }])
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-file" in html
    assert "sensors-error" in html
    assert "TS2322" in html


def test_format_failures_html_no_details_fallback():
    parser = TscParser()
    result = _result(errors=2)
    html = parser.format_failures_html(result)
    assert "no details available" in html
    assert "sensors-error" in html


# ---------------------------------------------------------------------------
# format_failures_llm
# ---------------------------------------------------------------------------

def test_format_failures_llm_success_empty():
    parser = TscParser()
    assert parser.format_failures_llm(_result(success=True)) == ""


def test_format_failures_llm_with_errors():
    parser = TscParser()
    result = _result(errors=1, error_list=[{
        "file": "src/index.ts", "line": 10, "column": 3,
        "code": "TS2322", "message": "Type 'string' not assignable",
    }])
    text = parser.format_failures_llm(result)
    assert "src/index.ts:10:3" in text
    assert "TS2322" in text


def test_format_failures_llm_no_details_fallback():
    parser = TscParser()
    result = _result(errors=1)
    assert "no details available" in parser.format_failures_llm(result)
