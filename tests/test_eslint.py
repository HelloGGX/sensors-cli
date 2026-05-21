"""Tests for ESLintParser — parse_output, calculate_score, and all format methods."""

import json
from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers.eslint import ESLintParser

# ---------------------------------------------------------------------------
# Fixtures / shared sample data
# ---------------------------------------------------------------------------

TRIGGERED_RULES = [
    {
        "ruleId": "@typescript-eslint/no-explicit-any",
        "guidance": (
            "About @typescript-eslint/no-explicit-any:\n"
            "  We want things to be typed.\n"
            "  Suppress with: // eslint-disable-next-line @typescript-eslint/no-explicit-any -- (reason)"
        ),
    }
]

ESLINT_ARRAY_OUTPUT = json.dumps([
    {
        "filePath": "/app/src/foo.ts",
        "errorCount": 1,
        "warningCount": 1,
        "messages": [
            {"line": 10, "column": 5, "severity": 2, "ruleId": "no-unused-vars", "message": "x is defined but never used"},
            {"line": 20, "column": 1, "severity": 1, "ruleId": "no-console", "message": "Unexpected console statement"},
        ],
    },
    {
        "filePath": "/app/src/bar.ts",
        "errorCount": 1,
        "warningCount": 0,
        "messages": [
            {"line": 3, "column": 9, "severity": 2, "ruleId": "@typescript-eslint/no-explicit-any", "message": "Unexpected any"},
        ],
    },
])

ESLINT_SUMMARY_OUTPUT = json.dumps({
    "results": [
        {
            "filePath": "/app/src/baz.ts",
            "errorCount": 2,
            "warningCount": 1,
            "messages": [
                {"line": 5, "column": 3, "severity": 2, "ruleId": "eqeqeq", "message": "Expected '===' but got '=='"},
                {"line": 8, "column": 1, "severity": 2, "ruleId": "no-undef", "message": "'foo' is not defined"},
                {"line": 12, "column": 7, "severity": 1, "ruleId": "no-console", "message": "Unexpected console statement"},
            ],
        }
    ],
    "summary": {
        "totalErrors": 8,
        "totalWarnings": 12,
        "triggeredRules": TRIGGERED_RULES,
    },
})

ESLINT_FILES_KEY_OUTPUT = json.dumps({
    "files": [
        {
            "filePath": "/app/src/baz.ts",
            "messages": [
                {"line": 5, "column": 3, "severity": 2, "ruleId": "no-undef", "message": "'module' is not defined."},
            ],
        }
    ],
    "summary": {
        "totalErrors": 8,
        "totalWarnings": 12,
        "triggeredRules": TRIGGERED_RULES,
    },
})

ESLINT_CLEAN_OUTPUT = json.dumps([
    {"filePath": "/app/src/clean.ts", "errorCount": 0, "warningCount": 0, "messages": []},
])


def _result(*, errors=0, warnings=0, violations=None, triggered_rules=None, success=None):
    if success is None:
        success = errors == 0 and warnings == 0
    return RunnerResult(
        timestamp=datetime.utcnow(),
        success=success,
        output={
            "errorCount": errors,
            "warningCount": warnings,
            "violations": violations or [],
            "triggeredRules": triggered_rules or [],
        },
    )


# ---------------------------------------------------------------------------
# parse_output
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_parse_array_format():
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_ARRAY_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 2
    assert result.output["warningCount"] == 1
    assert len(result.output["violations"]) == 3
    assert result.output["triggeredRules"] == []


@pytest.mark.asyncio
async def test_parse_array_format_violation_fields():
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_ARRAY_OUTPUT)

    v0 = result.output["violations"][0]
    assert v0["file"] == "/app/src/foo.ts"
    assert v0["line"] == 10
    assert v0["column"] == 5
    assert v0["ruleId"] == "no-unused-vars"
    assert v0["severity"] == 2

    v2 = result.output["violations"][2]
    assert v2["file"] == "/app/src/bar.ts"
    assert v2["ruleId"] == "@typescript-eslint/no-explicit-any"


@pytest.mark.asyncio
async def test_parse_summary_format_uses_summary_counts():
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_SUMMARY_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 8
    assert result.output["warningCount"] == 12


@pytest.mark.asyncio
async def test_parse_summary_format_triggered_rules():
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_SUMMARY_OUTPUT)

    rules = result.output["triggeredRules"]
    assert len(rules) == 1
    assert rules[0]["ruleId"] == "@typescript-eslint/no-explicit-any"
    assert "guidance" in rules[0]


@pytest.mark.asyncio
async def test_parse_files_key_format():
    """Custom formatters may use 'files' instead of 'results' for the per-file array."""
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_FILES_KEY_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 8
    assert result.output["warningCount"] == 12
    assert len(result.output["violations"]) == 1
    assert result.output["violations"][0]["ruleId"] == "no-undef"
    assert len(result.output["triggeredRules"]) == 1


@pytest.mark.asyncio
async def test_parse_warnings_only_is_failure():
    """Runs with only warnings should be treated as failures."""
    warnings_only = json.dumps([
        {
            "filePath": "/app/src/foo.ts",
            "errorCount": 0,
            "warningCount": 2,
            "messages": [
                {"line": 1, "column": 1, "severity": 1, "ruleId": "no-console", "message": "console.log"},
                {"line": 2, "column": 1, "severity": 1, "ruleId": "no-console", "message": "console.log"},
            ],
        }
    ])
    parser = ESLintParser()
    result = await parser.parse_output(warnings_only)

    assert result.success is False
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 2


@pytest.mark.asyncio
async def test_parse_clean_output():
    parser = ESLintParser()
    result = await parser.parse_output(ESLINT_CLEAN_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_strips_npm_preamble():
    """npm script header lines before the JSON must be ignored."""
    preamble = (
        "\n> rest-express@1.0.0 lint:json\n"
        "> eslint . --format json\n\n"
    )
    parser = ESLintParser()
    result = await parser.parse_output(preamble + ESLINT_FILES_KEY_OUTPUT)

    assert result.output["errorCount"] == 8
    assert result.output["warningCount"] == 12
    assert len(result.output["violations"]) == 1


@pytest.mark.asyncio
async def test_parse_invalid_json():
    parser = ESLintParser()
    result = await parser.parse_output("not json at all")

    assert result.success is False
    assert "parseError" in result.output


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

def test_calculate_score_errors_and_warnings():
    parser = ESLintParser()
    result = _result(errors=3, warnings=2)
    score = parser.calculate_score(result)
    assert score.value == 5
    assert score.direction == "less"


def test_calculate_score_clean():
    parser = ESLintParser()
    result = _result(errors=0, warnings=0)
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"


# ---------------------------------------------------------------------------
# format_details_terminal
# ---------------------------------------------------------------------------

def test_format_details_terminal_errors():
    parser = ESLintParser()
    text = parser.format_details_terminal(_result(errors=8, warnings=12))
    assert "[red]" in text
    assert "8 errors" in text
    assert "12 warnings" in text


def test_format_details_terminal_warnings_only():
    parser = ESLintParser()
    text = parser.format_details_terminal(_result(warnings=3))
    assert "[yellow]" in text
    assert "3 warnings" in text


def test_format_details_terminal_clean():
    parser = ESLintParser()
    text = parser.format_details_terminal(_result())
    assert "[green]" in text
    assert "No issues" in text


def test_format_details_terminal_includes_triggered_rule_ids():
    parser = ESLintParser()
    text = parser.format_details_terminal(_result(errors=1, triggered_rules=TRIGGERED_RULES))
    assert "@typescript-eslint/no-explicit-any" in text


# ---------------------------------------------------------------------------
# format_details_html
# ---------------------------------------------------------------------------

def test_format_details_html_errors():
    parser = ESLintParser()
    html = parser.format_details_html(_result(errors=2))
    assert "sensors-error" in html
    assert "2 errors" in html


def test_format_details_html_warnings():
    parser = ESLintParser()
    html = parser.format_details_html(_result(warnings=1))
    assert "sensors-warn" in html


def test_format_details_html_clean():
    parser = ESLintParser()
    html = parser.format_details_html(_result())
    assert "sensors-success" in html


# ---------------------------------------------------------------------------
# format_details_llm
# ---------------------------------------------------------------------------

def test_format_details_llm_errors():
    parser = ESLintParser()
    assert "8 errors" in parser.format_details_llm(_result(errors=8, warnings=12))


def test_format_details_llm_clean():
    parser = ESLintParser()
    assert parser.format_details_llm(_result()) == "No issues"


# ---------------------------------------------------------------------------
# format_failures_terminal
# ---------------------------------------------------------------------------

def test_format_failures_terminal_success_returns_empty():
    parser = ESLintParser()
    assert parser.format_failures_terminal(_result(success=True)) == ""


def test_format_failures_terminal_with_violations():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "/app/src/foo.ts", "line": 10, "column": 5,
        "severity": 2, "ruleId": "no-unused-vars", "message": "x is unused",
    }])
    text = parser.format_failures_terminal(result)
    assert "/app/src/foo.ts:10:5" in text
    assert "no-unused-vars" in text
    assert "x is unused" in text
    assert "[red]" in text


def test_format_failures_terminal_warning_violation():
    parser = ESLintParser()
    result = _result(errors=0, warnings=1, violations=[{
        "file": "/app/src/bar.ts", "line": 3, "column": 1,
        "severity": 1, "ruleId": "no-console", "message": "console.log",
    }])
    text = parser.format_failures_terminal(result)
    assert "[yellow]" in text
    assert "WARN" in text


def test_format_failures_terminal_no_violations_fallback():
    parser = ESLintParser()
    result = _result(errors=3, warnings=2)
    text = parser.format_failures_terminal(result)
    assert "no details available" in text
    assert "[red]" in text


def test_format_failures_terminal_triggered_rules_section():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "f.ts", "line": 1, "column": 1,
        "severity": 2, "ruleId": "@typescript-eslint/no-explicit-any", "message": "any",
    }], triggered_rules=TRIGGERED_RULES)
    text = parser.format_failures_terminal(result)
    assert "Triggered rules:" in text
    assert "@typescript-eslint/no-explicit-any" in text
    assert "We want things to be typed" in text


def test_format_failures_terminal_no_triggered_rules_section_when_empty():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "f.ts", "line": 1, "column": 1,
        "severity": 2, "ruleId": "eqeqeq", "message": "use ===",
    }])
    text = parser.format_failures_terminal(result)
    assert "Triggered rules:" not in text


# ---------------------------------------------------------------------------
# format_failures_html
# ---------------------------------------------------------------------------

def test_format_failures_html_success_returns_empty():
    parser = ESLintParser()
    assert parser.format_failures_html(_result(success=True)) == ""


def test_format_failures_html_with_violations():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "/app/src/foo.ts", "line": 10, "column": 5,
        "severity": 2, "ruleId": "no-unused-vars", "message": "x is unused",
    }])
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-file" in html
    assert "sensors-error" in html
    assert "/app/src/foo.ts:10:5" in html
    assert "no-unused-vars" in html


def test_format_failures_html_no_violations_fallback():
    parser = ESLintParser()
    result = _result(errors=2, warnings=1)
    html = parser.format_failures_html(result)
    assert "no details available" in html
    assert "sensors-error" in html


# ---------------------------------------------------------------------------
# format_failures_llm
# ---------------------------------------------------------------------------

def test_format_failures_llm_success_returns_empty():
    parser = ESLintParser()
    assert parser.format_failures_llm(_result(success=True)) == ""


def test_format_failures_llm_with_violations():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "cli.ts", "line": 26, "column": 9,
        "severity": 2, "ruleId": "no-undef", "message": "'x' is not defined",
    }])
    text = parser.format_failures_llm(result)
    assert "cli.ts:26:9" in text
    assert "no-undef" in text
    assert "'x' is not defined" in text


def test_format_failures_llm_no_violations_fallback():
    parser = ESLintParser()
    result = _result(errors=1)
    assert "no details available" in parser.format_failures_llm(result)


def test_format_failures_llm_triggered_rules():
    parser = ESLintParser()
    result = _result(errors=1, violations=[{
        "file": "f.ts", "line": 1, "column": 1,
        "severity": 2, "ruleId": "@typescript-eslint/no-explicit-any", "message": "any",
    }], triggered_rules=TRIGGERED_RULES)
    text = parser.format_failures_llm(result)
    assert "Correction guidance:" in text
    assert "@typescript-eslint/no-explicit-any" in text
    assert "We want things to be typed" in text


def test_format_failures_llm_skips_guidance_section_without_guidance():
    parser = ESLintParser()
    result = _result(errors=0, warnings=1, violations=[{
        "file": "f.ts", "line": 11, "column": 3,
        "severity": 1, "ruleId": "no-console", "message": "Unexpected console statement",
    }], triggered_rules=[{"ruleId": "no-console", "guidance": ""}])
    text = parser.format_failures_llm(result)
    assert "Correction guidance:" not in text
    assert "no-console" in text  # still in violation line


def test_format_failures_terminal_skips_triggered_rules_without_guidance():
    parser = ESLintParser()
    result = _result(errors=0, warnings=1, violations=[{
        "file": "f.ts", "line": 11, "column": 3,
        "severity": 1, "ruleId": "no-console", "message": "Unexpected console statement",
    }], triggered_rules=[{"ruleId": "no-console"}])
    text = parser.format_failures_terminal(result)
    assert "Triggered rules:" not in text
