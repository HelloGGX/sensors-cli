"""Tests for StylelintParser."""

import asyncio
import json
import os
import shutil
from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.stylelint import StylelintParser

STYLELINT_SAMPLE = r"""[{"source":"/Users/Shared/projects/dnd/dm-simulator/src/app/globals.css","deprecations":[],"invalidOptionWarnings":[],"parseErrors":[],"errored":true,"warnings":[{"line":57,"column":1,"endLine":57,"endColumn":7,"rule":"at-rule-no-unknown","severity":"error","text":"Unknown at-rule \"@theme\" (at-rule-no-unknown)"},{"line":5,"column":20,"endLine":5,"endColumn":27,"rule":"color-hex-length","severity":"error","text":"Expected \"#eeeeee\" to be \"#eee\" (color-hex-length)"},{"line":6,"column":19,"endLine":6,"endColumn":26,"rule":"color-hex-length","severity":"error","text":"Expected \"#ffffff\" to be \"#fff\" (color-hex-length)"},{"line":8,"column":20,"endLine":8,"endColumn":27,"rule":"color-hex-length","severity":"error","text":"Expected \"#ffffff\" to be \"#fff\" (color-hex-length)"},{"line":13,"column":19,"endLine":13,"endColumn":26,"rule":"color-hex-length","severity":"error","text":"Expected \"#FFCC00\" to be \"#FC0\" (color-hex-length)"},{"line":18,"column":16,"endLine":18,"endColumn":23,"rule":"color-hex-length","severity":"error","text":"Expected \"#FFCC00\" to be \"#FC0\" (color-hex-length)"},{"line":22,"column":17,"endLine":22,"endColumn":24,"rule":"color-hex-length","severity":"error","text":"Expected \"#888888\" to be \"#888\" (color-hex-length)"},{"line":23,"column":23,"endLine":23,"endColumn":30,"rule":"color-hex-length","severity":"error","text":"Expected \"#bbbbbb\" to be \"#bbb\" (color-hex-length)"},{"line":29,"column":23,"endLine":29,"endColumn":30,"rule":"color-hex-length","severity":"error","text":"Expected \"#ffffff\" to be \"#fff\" (color-hex-length)"},{"line":30,"column":23,"endLine":30,"endColumn":30,"rule":"color-hex-length","severity":"error","text":"Expected \"#ffffff\" to be \"#fff\" (color-hex-length)"},{"line":33,"column":15,"endLine":33,"endColumn":22,"rule":"color-hex-length","severity":"error","text":"Expected \"#444444\" to be \"#444\" (color-hex-length)"},{"line":10,"column":3,"endLine":10,"endColumn":27,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":15,"column":3,"endLine":15,"endColumn":26,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":20,"column":3,"endLine":20,"endColumn":27,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":25,"column":3,"endLine":25,"endColumn":21,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":28,"column":3,"endLine":28,"endColumn":29,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":33,"column":3,"endLine":33,"endColumn":23,"rule":"custom-property-empty-line-before","severity":"error","text":"Expected no empty line before custom property (custom-property-empty-line-before)"},{"line":1,"column":9,"endLine":1,"endColumn":22,"rule":"import-notation","severity":"error","text":"Expected \"\"tailwindcss\"\" to be \"url(\"tailwindcss\")\" (import-notation)"}]}]"""


def _result(*, errors=0, warnings=0, violations=None, success=None):
    if success is None:
        success = errors == 0 and warnings == 0
    return RunnerResult(
        timestamp=datetime.utcnow(),
        success=success,
        output={
            "errorCount": errors,
            "warningCount": warnings,
            "violations": violations or [],
        },
    )


@pytest.mark.asyncio
async def test_parse_sample_from_user():
    parser = StylelintParser()
    result = await parser.parse_output(STYLELINT_SAMPLE)

    assert result.success is False
    assert result.output["errorCount"] == 18
    assert result.output["warningCount"] == 0
    assert len(result.output["violations"]) == 18
    v0 = result.output["violations"][0]
    assert v0["file"].endswith("globals.css")
    assert v0["line"] == 57
    assert v0["ruleId"] == "at-rule-no-unknown"
    assert v0["severity"] == 2


@pytest.mark.asyncio
async def test_parse_clean_file():
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
    result = await parser.parse_output(data)
    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_warning_severity():
    data = json.dumps(
        [
            {
                "source": "/w.css",
                "parseErrors": [],
                "warnings": [
                    {
                        "line": 1,
                        "column": 1,
                        "rule": "no-descending-specificity",
                        "severity": "warning",
                        "text": "Minor issue",
                    }
                ],
            }
        ]
    )
    parser = StylelintParser()
    result = await parser.parse_output(data)
    assert result.success is False
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 1
    assert result.output["violations"][0]["severity"] == 1


@pytest.mark.asyncio
async def test_parse_parse_errors():
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
    result = await parser.parse_output(data)
    assert result.success is False
    assert result.output["errorCount"] == 1
    assert result.output["violations"][0]["severity"] == 2
    assert "Unclosed" in result.output["violations"][0]["message"]


@pytest.mark.asyncio
async def test_parse_strips_npm_preamble():
    preamble = "\n> dm-simulator@0.1.0 lintcss:sensor\n> npx stylelint --formatter json\n\n"
    parser = StylelintParser()
    result = await parser.parse_output(preamble + STYLELINT_SAMPLE)
    assert result.output["errorCount"] == 18
    assert len(result.output["violations"]) == 18


@pytest.mark.asyncio
async def test_parse_invalid_json():
    parser = StylelintParser()
    result = await parser.parse_output("not json")
    assert result.success is False
    assert "parseError" in result.output


@pytest.mark.asyncio
async def test_parse_non_array_json():
    parser = StylelintParser()
    result = await parser.parse_output('{"foo": 1}')
    assert result.success is False
    assert "Expected top-level JSON array" in result.output.get("parseError", "")


def test_calculate_score():
    parser = StylelintParser()
    assert parser.calculate_score(_result(errors=2, warnings=1)).value == 3


def test_format_details_and_failures():
    parser = StylelintParser()
    bad = _result(
        errors=1,
        violations=[
            {
                "file": "/x.css",
                "line": 1,
                "column": 1,
                "severity": 2,
                "ruleId": "color-hex-length",
                "message": "bad hex",
            }
        ],
    )
    assert "[red]" in parser.format_details_terminal(bad)
    assert "sensors-error" in parser.format_details_html(bad)
    assert parser.format_details_llm(bad) == "1 error"
    assert parser.format_failures_terminal(_result(success=True)) == ""
    ft = parser.format_failures_terminal(bad)
    assert "/x.css:1:1" in ft
    assert "color-hex-length" in ft
    assert "no details" not in parser.format_failures_llm(bad)


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
    result = await parser.parse_output(output)
    assert result.output["errorCount"] > 0
    assert len(result.output["violations"]) == result.output["errorCount"]
    assert "no details" not in parser.format_failures_llm(result)
