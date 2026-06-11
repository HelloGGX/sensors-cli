"""Tests for StylelintParser parse() output."""

import asyncio
import json
import os
import shutil

import pytest

from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.stylelint import StylelintParser

STYLELINT_SAMPLE = json.dumps(
    [
        {
            "source": "/Users/Shared/projects/app/globals.css",
            "deprecations": [],
            "invalidOptionWarnings": [],
            "parseErrors": [],
            "errored": True,
            "warnings": [
                {
                    "line": 57,
                    "column": 1,
                    "rule": "at-rule-no-unknown",
                    "severity": "error",
                    "text": "Unknown at-rule \"@theme\" (at-rule-no-unknown)",
                },
                {
                    "line": 5,
                    "column": 20,
                    "rule": "color-hex-length",
                    "severity": "warning",
                    "text": "Expected \"#eeeeee\" to be \"#eee\" (color-hex-length)",
                },
            ],
        }
    ]
)


def test_parse_sample():
    parser = StylelintParser()
    parsed = parser.parse(STYLELINT_SAMPLE)

    assert parsed.success is False
    assert parsed.summary == "1 error, 1 warning"
    assert parsed.score.value == 2

    error_count = next(m for m in parsed.metrics if m.key == "errorCount")
    warning_count = next(m for m in parsed.metrics if m.key == "warningCount")
    assert error_count.value == 1
    assert warning_count.value == 1
    assert len(parsed.findings) == 2

    f0 = parsed.findings[0]
    assert f0.file.endswith("globals.css")
    assert f0.line == 57
    assert f0.rule == "at-rule-no-unknown"
    assert f0.severity == "error"


def test_parse_clean_file():
    data = json.dumps(
        [
            {
                "source": "/app/a.css",
                "deprecations": [],
                "invalidOptionWarnings": [],
                "parseErrors": [],
                "errored": False,
                "warnings": [],
            }
        ]
    )
    parser = StylelintParser()
    parsed = parser.parse(data)

    assert parsed.success is True
    assert parsed.summary == "No issues"
    assert parsed.findings == []


def test_parse_parse_errors():
    data = json.dumps(
        [
            {
                "source": "/broken.css",
                "parseErrors": [{"line": 2, "column": 1, "text": "Unclosed block"}],
                "warnings": [],
            }
        ]
    )
    parser = StylelintParser()
    parsed = parser.parse(data)

    assert parsed.success is False
    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "error"
    assert "Unclosed" in parsed.findings[0].message


def test_parse_strips_npm_preamble():
    preamble = "\n> app@0.1.0 lintcss:sensor\n> npx stylelint --formatter json\n\n"
    parser = StylelintParser()
    parsed = parser.parse(preamble + STYLELINT_SAMPLE)

    assert len(parsed.findings) == 2


def test_parse_invalid_json():
    parser = StylelintParser()
    parsed = parser.parse("not json")

    assert parsed.success is False
    assert "parseError" in parsed.extra


def test_parse_non_array_json():
    parser = StylelintParser()
    parsed = parser.parse('{"foo": 1}')

    assert parsed.success is False
    assert "Expected top-level JSON array" in parsed.extra.get("parseError", "")


@pytest.mark.asyncio
async def test_integration_stylelint_full_pipeline(tmp_path):
    if shutil.which("npx") is None:
        pytest.skip("npx not available")
    rc = tmp_path / ".stylelintrc.json"
    rc.write_text(json.dumps({"rules": {"color-hex-length": "short"}}), encoding="utf-8")
    css = tmp_path / "bad.css"
    css.write_text(".x { color: #ffffff; }\n", encoding="utf-8")
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    proc = await asyncio.create_subprocess_exec(
        "npx",
        "--yes",
        "stylelint",
        "--formatter",
        "json",
        str(css),
        cwd=str(tmp_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await proc.communicate()
    output = GenericRunner.strip_ansi(stdout.decode("utf-8", errors="ignore"))
    parser = StylelintParser()
    parsed = parser.parse(output)

    error_count = next(m for m in parsed.metrics if m.key == "errorCount")
    assert error_count.value > 0
    assert len(parsed.findings) == error_count.value
