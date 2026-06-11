import json

import pytest

from sensors.runners.parsers.default import DefaultParser, _extract_json

PARSER = DefaultParser()


# ---------------------------------------------------------------------------
# _extract_json unit tests
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"findings": []}') == {"findings": []}


def test_extract_json_with_preamble():
    raw = 'Some tool banner\nwarning: foo\n{"findings": []}'
    assert _extract_json(raw) == {"findings": []}


def test_extract_json_with_postamble():
    raw = '{"findings": []}\nDone in 0.5s'
    assert _extract_json(raw) == {"findings": []}


def test_extract_json_preamble_and_postamble():
    raw = 'Starting...\n{"findings": [{"message": "oops"}]}\nFinished.'
    assert _extract_json(raw) == {"findings": [{"message": "oops"}]}


def test_extract_json_no_object_raises():
    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json("no json here")


def test_extract_json_unterminated_raises():
    with pytest.raises(ValueError, match="Unterminated"):
        _extract_json('{"findings": [')


def test_extract_json_string_with_braces():
    raw = '{"message": "found { unexpected } brace"}'
    assert _extract_json(raw) == {"message": "found { unexpected } brace"}


# ---------------------------------------------------------------------------
# parse: derivation rules and explicit overrides
# ---------------------------------------------------------------------------

def test_parse_empty_findings_derives_success_summary_and_score():
    parsed = PARSER.parse('{"findings": []}')
    assert parsed.success is True
    assert parsed.summary == "No issues"
    assert parsed.score.value == 0
    assert parsed.score.direction == "less"


def test_parse_derives_from_findings_when_fields_absent():
    payload = json.dumps({"findings": [{"message": "bad thing"}]})
    parsed = PARSER.parse(payload)
    assert parsed.success is False
    assert parsed.summary == "1 issue"
    assert parsed.score.value == 1


def test_parse_explicit_overrides_are_used():
    payload = json.dumps({
        "success": True,
        "summary": "Coverage 72% (threshold 80%)",
        "score": {"value": 72, "direction": "more", "description": "Coverage percent"},
        "findings": [{"message": "warn"}],
    })
    parsed = PARSER.parse(payload)
    assert parsed.success is True
    assert parsed.summary == "Coverage 72% (threshold 80%)"
    assert parsed.score.value == 72
    assert parsed.score.direction == "more"
    assert parsed.score.description == "Coverage percent"


def test_parse_score_direction_defaults_to_less():
    payload = json.dumps({"score": {"value": 5}})
    parsed = PARSER.parse(payload)
    assert parsed.score.direction == "less"


# ---------------------------------------------------------------------------
# parse: typed sections
# ---------------------------------------------------------------------------

def test_parse_builds_findings_metrics_guidance_and_extra():
    payload = json.dumps({
        "findings": [
            {
                "message": "unused variable",
                "severity": "warning",
                "file": "src/foo.py",
                "line": 42,
                "column": 9,
                "rule": "F841",
                "context": "x is never used",
            }
        ],
        "metrics": [
            {
                "key": "coverage",
                "label": "Coverage",
                "value": 72,
                "unit": "%",
                "direction": "more",
                "threshold": 80,
            }
        ],
        "guidance": [
            {
                "rule": "F841",
                "summary": "Unused variable",
                "body": "Remove variable or use it.",
            }
        ],
        "extra": {"rawTable": [{"file": "src/foo.py", "pct": 72}]},
    })

    parsed = PARSER.parse(payload)

    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "warning"
    assert parsed.findings[0].file == "src/foo.py"
    assert parsed.findings[0].line == 42
    assert parsed.findings[0].column == 9
    assert parsed.findings[0].rule == "F841"
    assert parsed.findings[0].context == "x is never used"

    assert len(parsed.metrics) == 1
    assert parsed.metrics[0].key == "coverage"
    assert parsed.metrics[0].direction == "more"
    assert parsed.metrics[0].threshold == 80

    assert len(parsed.guidance) == 1
    assert parsed.guidance[0].rule == "F841"
    assert parsed.guidance[0].summary == "Unused variable"

    assert parsed.extra["rawTable"][0]["pct"] == 72


def test_parse_invalid_metric_rows_are_dropped():
    payload = json.dumps({
        "metrics": [
            {"key": "", "label": "Missing key", "value": 1},
            {"key": "valid", "label": "Valid", "value": 2},
        ]
    })
    parsed = PARSER.parse(payload)
    assert len(parsed.metrics) == 1
    assert parsed.metrics[0].key == "valid"


# ---------------------------------------------------------------------------
# parse: tolerance and error handling
# ---------------------------------------------------------------------------

def test_parse_tolerates_preamble():
    payload = 'tool banner\n{"findings": [{"message": "oops", "file": "x.py", "line": 1}]}'
    parsed = PARSER.parse(payload)
    assert parsed.success is False
    assert parsed.findings[0].file == "x.py"


def test_parse_no_json_returns_parse_error_output():
    parsed = PARSER.parse("nothing here")
    assert parsed.success is False
    assert "Parse error:" in parsed.summary
    assert "parseError" in parsed.extra
