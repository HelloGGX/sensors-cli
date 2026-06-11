"""Tests for ESLintParser — parse_output, calculate_score, and all format methods."""

import json
from datetime import datetime

import pytest

from sensors.config import RunnerResult
from sensors.config.result_types import Finding, GuidanceBlock
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
# parse  (new structured interface)
# ---------------------------------------------------------------------------

def test_parse_array_format_returns_parsed_output():
    parser = ESLintParser()
    parsed = parser.parse(ESLINT_ARRAY_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "2 errors, 1 warning"
    assert parsed.score.value == 3
    assert parsed.score.direction == "less"

    errors = [m for m in parsed.metrics if m.key == "errorCount"]
    assert len(errors) == 1
    assert errors[0].value == 2

    warnings = [m for m in parsed.metrics if m.key == "warningCount"]
    assert warnings[0].value == 1


def test_parse_findings_from_array():
    parser = ESLintParser()
    parsed = parser.parse(ESLINT_ARRAY_OUTPUT)

    assert len(parsed.findings) == 3
    f0 = parsed.findings[0]
    assert f0.file == "/app/src/foo.ts"
    assert f0.line == 10
    assert f0.column == 5
    assert f0.rule == "no-unused-vars"
    assert f0.severity == "error"

    f1 = parsed.findings[1]
    assert f1.severity == "warning"
    assert f1.rule == "no-console"


def test_parse_summary_format_uses_summary_counts():
    parser = ESLintParser()
    parsed = parser.parse(ESLINT_SUMMARY_OUTPUT)

    assert parsed.success is False
    errors = next(m for m in parsed.metrics if m.key == "errorCount")
    warnings = next(m for m in parsed.metrics if m.key == "warningCount")
    assert errors.value == 8
    assert warnings.value == 12


def test_parse_triggered_rules_become_guidance():
    parser = ESLintParser()
    parsed = parser.parse(ESLINT_SUMMARY_OUTPUT)

    assert len(parsed.guidance) == 1
    g = parsed.guidance[0]
    assert g.rule == "@typescript-eslint/no-explicit-any"
    assert "We want things to be typed" in g.body


def test_parse_clean_is_success():
    parser = ESLintParser()
    parsed = parser.parse(ESLINT_CLEAN_OUTPUT)

    assert parsed.success is True
    assert parsed.summary == "No issues"
    assert parsed.findings == []
    assert parsed.guidance == []


def test_parse_invalid_json_returns_failure():
    parser = ESLintParser()
    parsed = parser.parse("not json")

    assert parsed.success is False
    assert "parseError" in parsed.extra


def test_parse_finding_uses_short_text_when_present():
    eslint_with_shorttext = json.dumps({
        "files": [
            {
                "filePath": "/app/src/index.ts",
                "messages": [
                    {
                        "line": 19,
                        "column": 3,
                        "severity": 1,
                        "ruleId": "no-console",
                        "message": "Unexpected console statement.",
                        "shortText": "Use `logger` from `server/logger.ts` instead",
                    }
                ],
            }
        ],
        "summary": {"totalErrors": 0, "totalWarnings": 1, "triggeredRules": []},
    })
    parser = ESLintParser()
    parsed = parser.parse(eslint_with_shorttext)

    assert parsed.findings[0].message == "Use `logger` from `server/logger.ts` instead"
