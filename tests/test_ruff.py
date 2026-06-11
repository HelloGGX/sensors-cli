import json

from sensors.runners.parsers.ruff import GUIDANCE_JSON_KEY, RuffParser


RUFF_FAILURE_OUTPUT = """\
B904 Within an except clause, raise exceptions with raise ... from err
  --> sensors/cli.py:26:9

E501 Line too long (103 > 100)
  --> sensors/config/loader.py:66:101

Found 2 errors.
"""


def test_parse_text_failure():
    parser = RuffParser()
    parsed = parser.parse(RUFF_FAILURE_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "2 issues"
    assert parsed.score.value == 2
    assert parsed.score.direction == "less"
    assert len(parsed.findings) == 2

    f0 = parsed.findings[0]
    assert f0.rule == "B904"
    assert f0.file == "sensors/cli.py"
    assert f0.line == 26
    assert f0.column == 9


def test_parse_text_success():
    parser = RuffParser()
    parsed = parser.parse("All checks passed!\n")

    assert parsed.success is True
    assert parsed.summary == "No issues"
    assert parsed.findings == []


def test_parse_json_with_guidance():
    parser = RuffParser()
    output = json.dumps([
        {
            "code": "UP035",
            "message": "typing.List is deprecated",
            "filename": "sensors/config/schema.py",
            "location": {"row": 4, "column": 1},
        },
        {
            GUIDANCE_JSON_KEY: {
                "triggered": ["UP035"],
                "rules": {
                    "UP035": {
                        "short": "Use builtin generic types",
                        "guidance": "Replace typing.List with list",
                    }
                },
            }
        },
    ])

    parsed = parser.parse(output)
    assert parsed.success is False
    assert len(parsed.findings) == 1
    assert len(parsed.guidance) == 1
    assert parsed.guidance[0].rule == "UP035"
    assert "typing.List" in parsed.guidance[0].body


def test_parse_invalid_json_returns_parse_error():
    parser = RuffParser()
    parsed = parser.parse("[{not-valid}")

    assert parsed.success is False
    assert "parseError" in parsed.extra
