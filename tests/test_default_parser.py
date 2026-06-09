import asyncio
import json

import pytest

from sensors.runners.parsers.default import DefaultParser, _extract_json

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

PARSER = DefaultParser()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# _extract_json unit tests
# ---------------------------------------------------------------------------

def test_extract_json_plain():
    assert _extract_json('{"violations": []}') == {"violations": []}


def test_extract_json_with_preamble():
    raw = 'Some tool banner\nwarning: foo\n{"violations": []}'
    assert _extract_json(raw) == {"violations": []}


def test_extract_json_with_postamble():
    raw = '{"violations": []}\nDone in 0.5s'
    assert _extract_json(raw) == {"violations": []}


def test_extract_json_preamble_and_postamble():
    raw = 'Starting...\n{"violations": [{"message": "oops"}]}\nFinished.'
    assert _extract_json(raw) == {"violations": [{"message": "oops"}]}


def test_extract_json_no_object_raises():
    with pytest.raises(ValueError, match="No JSON object found"):
        _extract_json("no json here")


def test_extract_json_unterminated_raises():
    with pytest.raises(ValueError, match="Unterminated"):
        _extract_json('{"violations": [')


def test_extract_json_string_with_braces():
    # Braces inside string values must not confuse the brace counter.
    raw = '{"message": "found { unexpected } brace"}'
    assert _extract_json(raw) == {"message": "found { unexpected } brace"}


# ---------------------------------------------------------------------------
# parse_output: derivation rules
# ---------------------------------------------------------------------------

def test_parse_empty_violations():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert result.success is True
    assert result.output["summary"] == "No issues"
    assert result.output["scoreValue"] == 0
    assert result.output["scoreDirection"] == "less"


def test_parse_derives_success_from_violations():
    payload = json.dumps({"violations": [{"message": "bad thing"}]})
    result = run(PARSER.parse_output(payload))
    assert result.success is False
    assert result.output["scoreValue"] == 1


def test_parse_derives_summary_singular():
    payload = json.dumps({"violations": [{"message": "x"}]})
    result = run(PARSER.parse_output(payload))
    assert result.output["summary"] == "1 issue"


def test_parse_derives_summary_plural():
    violations = [{"message": "a"}, {"message": "b"}, {"message": "c"}]
    payload = json.dumps({"violations": violations})
    result = run(PARSER.parse_output(payload))
    assert result.output["summary"] == "3 issues"


def test_parse_missing_violations_key():
    result = run(PARSER.parse_output("{}"))
    assert result.success is True
    assert result.output["scoreValue"] == 0


# ---------------------------------------------------------------------------
# parse_output: explicit overrides win
# ---------------------------------------------------------------------------

def test_parse_explicit_success_overrides():
    # success=True even though there are violations
    payload = json.dumps({"success": True, "violations": [{"message": "warn"}]})
    result = run(PARSER.parse_output(payload))
    assert result.success is True


def test_parse_explicit_success_false_no_violations():
    payload = json.dumps({"success": False, "violations": []})
    result = run(PARSER.parse_output(payload))
    assert result.success is False


def test_parse_explicit_summary_used():
    payload = json.dumps({"summary": "Coverage 72% (threshold 80%)", "violations": []})
    result = run(PARSER.parse_output(payload))
    assert result.output["summary"] == "Coverage 72% (threshold 80%)"


def test_parse_explicit_score_used():
    payload = json.dumps({"score": {"value": 72, "direction": "more"}, "violations": []})
    result = run(PARSER.parse_output(payload))
    assert result.output["scoreValue"] == 72
    assert result.output["scoreDirection"] == "more"


def test_parse_score_direction_defaults_to_less():
    payload = json.dumps({"score": {"value": 5}})
    result = run(PARSER.parse_output(payload))
    assert result.output["scoreDirection"] == "less"


# ---------------------------------------------------------------------------
# parse_output: tolerance and error handling
# ---------------------------------------------------------------------------

def test_parse_tolerates_preamble():
    payload = 'tool banner\n{"violations": [{"message": "oops", "file": "x.py", "line": 1}]}'
    result = run(PARSER.parse_output(payload))
    assert result.success is False
    assert result.output["violations"][0]["file"] == "x.py"


def test_parse_no_json_returns_error_result():
    result = run(PARSER.parse_output("nothing here"))
    assert result.success is False
    assert "parseError" in result.output


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

def test_calculate_score_less():
    result = run(PARSER.parse_output(json.dumps({"violations": [{"message": "x"}]})))
    score = PARSER.calculate_score(result)
    assert score.value == 1
    assert score.direction == "less"


def test_calculate_score_more():
    payload = json.dumps({"score": {"value": 88, "direction": "more"}, "violations": []})
    result = run(PARSER.parse_output(payload))
    score = PARSER.calculate_score(result)
    assert score.value == 88
    assert score.direction == "more"


# ---------------------------------------------------------------------------
# Formatting: details
# ---------------------------------------------------------------------------

def test_format_details_terminal_success():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert "[green]" in PARSER.format_details_terminal(result)
    assert "No issues" in PARSER.format_details_terminal(result)


def test_format_details_terminal_failure():
    payload = json.dumps({"violations": [{"message": "bad"}]})
    result = run(PARSER.parse_output(payload))
    assert "[red]" in PARSER.format_details_terminal(result)


def test_format_details_html_success():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert 'sensors-success' in PARSER.format_details_html(result)


def test_format_details_html_failure():
    payload = json.dumps({"violations": [{"message": "bad"}]})
    result = run(PARSER.parse_output(payload))
    assert 'sensors-error' in PARSER.format_details_html(result)


def test_format_details_llm():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert PARSER.format_details_llm(result) == "No issues"


# ---------------------------------------------------------------------------
# Formatting: failures
# ---------------------------------------------------------------------------

def test_format_failures_terminal_empty_on_success():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert PARSER.format_failures_terminal(result) == ""


def test_format_failures_terminal_with_violations():
    violations = [{"message": "bad thing", "file": "src/foo.py", "line": 10, "rule": "X01"}]
    result = run(PARSER.parse_output(json.dumps({"violations": violations})))
    out = PARSER.format_failures_terminal(result)
    assert "src/foo.py:10" in out
    assert "X01" in out
    assert "bad thing" in out


def test_format_failures_terminal_no_detail_fallback():
    # success=False but no violations list -- should show summary fallback
    payload = json.dumps({"success": False, "summary": "Coverage 72%", "violations": []})
    result = run(PARSER.parse_output(payload))
    out = PARSER.format_failures_terminal(result)
    assert "Coverage 72%" in out


def test_format_failures_html_with_violations():
    violations = [{"message": "oops", "file": "a.py", "line": 5, "rule": "E1"}]
    result = run(PARSER.parse_output(json.dumps({"violations": violations})))
    out = PARSER.format_failures_html(result)
    assert "sensors-violation" in out
    assert "a.py:5" in out
    assert "oops" in out


def test_format_failures_llm_with_violations():
    violations = [
        {"message": "warn here", "severity": "warning", "file": "b.py", "line": 3},
    ]
    result = run(PARSER.parse_output(json.dumps({"violations": violations})))
    out = PARSER.format_failures_llm(result)
    assert "[WARNING]" in out
    assert "b.py:3" in out
    assert "warn here" in out


def test_format_failures_llm_empty_on_success():
    result = run(PARSER.parse_output('{"violations": []}'))
    assert PARSER.format_failures_llm(result) == ""
