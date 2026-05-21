import json
from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers import SemgrepParser

SEMGREP_FINDINGS_JSON = json.dumps({
    "results": [
        {
            "check_id": "javascript.express.security.audit.xss.mustache.var-in-href",
            "path": "src/views/index.ejs",
            "start": {"line": 12, "col": 5},
            "end": {"line": 12, "col": 40},
            "extra": {
                "message": "Detected a template variable used in an anchor tag href. This is a potential XSS vulnerability.",
                "severity": "WARNING",
            },
        },
        {
            "check_id": "javascript.lang.security.detect-eval-with-expression",
            "path": "src/utils/dynamic.ts",
            "start": {"line": 45, "col": 1},
            "end": {"line": 45, "col": 25},
            "extra": {
                "message": "Detected eval with a non-literal argument. This is a security risk.",
                "severity": "ERROR",
            },
        },
    ],
    "errors": [],
})

SEMGREP_CLEAN_JSON = json.dumps({
    "results": [],
    "errors": [],
})

SEMGREP_WITH_ERRORS_JSON = json.dumps({
    "results": [],
    "errors": [
        {"message": "Failed to parse file.ts", "type": "ParseError"},
    ],
})

# PartialParsing errors use a list type: ["PartialParsing", [...spans...]]
SEMGREP_WITH_PARTIAL_PARSING_JSON = json.dumps({
    "results": [],
    "errors": [
        {
            "code": 3,
            "level": "warn",
            "type": ["PartialParsing", [{"path": ".github/workflows/main.yml"}]],
            "message": "Syntax error at line .github/workflows/main.yml:67",
            "path": ".github/workflows/main.yml",
        },
    ],
})

# NeedLogin also uses a list type in some semgrep versions
SEMGREP_WITH_NEED_LOGIN_LIST_JSON = json.dumps({
    "results": [],
    "errors": [
        {"message": "Not logged in", "type": ["NeedLogin", {}]},
    ],
})


SEMGREP_MIXED_OUTPUT = (
    "\n"
    "Scanning 95 files tracked by git with 1064 Code rules:\n"
    "\n"
    "  Language      Rules   Files\n"
    "  ts              166      76\n"
    "\n"
    "  100% 0:00:00\n"
    "\n"
    + SEMGREP_CLEAN_JSON
    + "\n"
    "Ran 261 rules on 95 files: 0 findings.\n"
)


@pytest.mark.asyncio
async def test_parse_output_mixed_with_stderr():
    """Semgrep sends progress to stderr mixed with JSON stdout."""
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_MIXED_OUTPUT)

    assert result.success is True
    assert result.output["findingCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_output_with_findings():
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_FINDINGS_JSON)

    assert result.success is False
    assert result.output["findingCount"] == 2
    assert result.output["errorCount"] == 0
    assert len(result.output["violations"]) == 2

    v0 = result.output["violations"][0]
    assert v0["file"] == "src/views/index.ejs"
    assert v0["line"] == 12
    assert v0["column"] == 5
    assert v0["ruleId"] == "javascript.express.security.audit.xss.mustache.var-in-href"
    assert v0["severity"] == "WARNING"

    v1 = result.output["violations"][1]
    assert v1["file"] == "src/utils/dynamic.ts"
    assert v1["line"] == 45
    assert v1["severity"] == "ERROR"


@pytest.mark.asyncio
async def test_parse_output_clean():
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_CLEAN_JSON)

    assert result.success is True
    assert result.output["findingCount"] == 0
    assert result.output["errorCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_output_with_errors():
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_WITH_ERRORS_JSON)

    assert result.success is False
    assert result.output["findingCount"] == 0
    assert result.output["errorCount"] == 1
    assert result.output["errors"][0]["message"] == "Failed to parse file.ts"


@pytest.mark.asyncio
async def test_parse_output_partial_parsing_is_ignored():
    """PartialParsing errors (list-typed) should be ignored -- scan still succeeded."""
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_WITH_PARTIAL_PARSING_JSON)

    assert result.success is True
    assert result.output["findingCount"] == 0
    assert result.output["errorCount"] == 0


@pytest.mark.asyncio
async def test_parse_output_need_login_list_type_is_ignored():
    """NeedLogin encoded as a list type should also be ignored."""
    parser = SemgrepParser()
    result = await parser.parse_output(SEMGREP_WITH_NEED_LOGIN_LIST_JSON)

    assert result.success is True
    assert result.output["errorCount"] == 0


@pytest.mark.asyncio
async def test_parse_output_invalid_json():
    parser = SemgrepParser()
    result = await parser.parse_output("not json at all")

    assert result.success is False
    assert "parseError" in result.output


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = SemgrepParser()
    result = await parser.parse_output("")

    assert result.success is False
    assert "parseError" in result.output


def test_calculate_score():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"findingCount": 3, "errorCount": 1, "violations": [], "errors": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 4
    assert score.direction == "less"


def test_calculate_score_clean():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"findingCount": 0, "errorCount": 0, "violations": [], "errors": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"


# -- Details formatting --


def test_format_details_terminal_findings():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"findingCount": 2, "errorCount": 0, "violations": [], "errors": []},
    )
    text = parser.format_details_terminal(result)
    assert "[red]" in text
    assert "2 findings" in text


def test_format_details_terminal_clean():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"findingCount": 0, "errorCount": 0, "violations": [], "errors": []},
    )
    text = parser.format_details_terminal(result)
    assert "[green]" in text
    assert "No findings" in text


def test_format_details_html_findings():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"findingCount": 1, "errorCount": 0, "violations": [], "errors": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-error" in html
    assert "1 finding" in html


def test_format_details_html_clean():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"findingCount": 0, "errorCount": 0, "violations": [], "errors": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-success" in html


def test_format_details_llm():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"findingCount": 2, "errorCount": 1, "violations": [], "errors": []},
    )
    text = parser.format_details_llm(result)
    assert text == "2 findings, 1 error"


# -- Failures formatting --


def test_format_failures_success_returns_empty():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"findingCount": 0, "errorCount": 0, "violations": [], "errors": []},
    )
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_terminal():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "findingCount": 1,
            "errorCount": 0,
            "violations": [{
                "file": "app.ts",
                "line": 10,
                "column": 1,
                "ruleId": "typescript.security.detect-eval",
                "message": "eval is dangerous",
                "severity": "ERROR",
            }],
            "errors": [],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "app.ts:10:1" in text
    assert "ERROR" in text
    assert "detect-eval" in text
    assert "[red]" in text


def test_format_failures_terminal_warning():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "findingCount": 1,
            "errorCount": 0,
            "violations": [{
                "file": "app.ts",
                "line": 5,
                "column": 3,
                "ruleId": "some-rule",
                "message": "minor issue",
                "severity": "WARNING",
            }],
            "errors": [],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "[yellow]" in text
    assert "WARNING" in text


def test_format_failures_html():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "findingCount": 1,
            "errorCount": 0,
            "violations": [{
                "file": "src/index.ts",
                "line": 20,
                "column": 5,
                "ruleId": "javascript.lang.security.detect-eval",
                "message": "eval detected",
                "severity": "ERROR",
            }],
            "errors": [],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-file" in html
    assert "sensors-error" in html
    assert "sensors-rule" in html
    assert "src/index.ts:20:5" in html


def test_format_failures_html_warning():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "findingCount": 1,
            "errorCount": 0,
            "violations": [{
                "file": "a.ts",
                "line": 1,
                "column": 1,
                "ruleId": "rule",
                "message": "msg",
                "severity": "WARNING",
            }],
            "errors": [],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-warn" in html


def test_format_failures_llm():
    parser = SemgrepParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "findingCount": 1,
            "errorCount": 0,
            "violations": [{
                "file": "lib/api.ts",
                "line": 33,
                "column": 8,
                "ruleId": "typescript.security.sql-injection",
                "message": "Possible SQL injection",
                "severity": "ERROR",
            }],
            "errors": [],
        },
    )
    text = parser.format_failures_llm(result)
    assert "lib/api.ts:33:8" in text
    assert "ERROR" in text
    assert "sql-injection" in text
    assert "Possible SQL injection" in text
