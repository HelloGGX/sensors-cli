import asyncio
import json
import os
from datetime import datetime

import pytest

from sensors.config import RunnerResult
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.ruff import GUIDANCE_JSON_KEY, RuffParser

RUFF_FAILURE_OUTPUT = """\
B904 Within an `except` clause, raise exceptions with `raise ... from err` or `raise ... from None` to distinguish them from errors in exception handling
  --> sensors/cli.py:26:9
   |
24 |     except ConfigLoadError as e:
25 |         typer.echo(f"Error: {e}", err=True)
26 |         raise typer.Exit(code=2)
   |         ^^^^^^^^^^^^^^^^^^^^^^^^
   |

E501 Line too long (103 > 100)
  --> sensors/config/loader.py:66:101
   |
64 |         if not working_dir.is_dir():
65 |             raise ConfigLoadError(
66 |                 f"Working directory for runner '{runner.name}' is not a directory: {runner.workingDir}"
   |                                                                                                     ^^^
67 |             )
   |

UP035 `typing.List` is deprecated, use `list` instead
 --> sensors/config/schema.py:4:1
  |
3 | from enum import Enum
4 | from typing import List, Optional
  | ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
5 |
6 | from pydantic import BaseModel, Field, field_validator
  |

Found 3 errors.
No fixes available (2 hidden fixes can be enabled with the `--unsafe-fixes` option).
"""

RUFF_SUCCESS_OUTPUT = """\
All checks passed!
"""

RUFF_EMPTY_OUTPUT = ""

RUFF_SECURITY_OUTPUT_WITH_HYPERLINKS = (
    "\x1b]8;;https://docs.astral.sh/ruff/rules/try-except-pass\x1b\\S110\x1b]8;;\x1b\\"
    " `try`-`except`-`pass` detected, consider logging the exception\n"
    "   --> sensors/runners/generic.py:244:25\n"
    "    |\n"
    "242 |                               process.kill()\n"
    "243 |                               await asyncio.wait_for(process.wait(), timeout=1.0)\n"
    "244 | /                         except Exception:\n"
    "245 | |                             # Process cleanup failed, but we're shutting down anyway\n"
    "246 | |                             pass\n"
    "    | |________________________________^\n"
    "247 |                       except Exception:\n"
    "248 |                           # Process already dead or other error, ignore\n"
    "    |\n"
    "\n"
    "\x1b]8;;https://docs.astral.sh/ruff/rules/try-except-pass\x1b\\S110\x1b]8;;\x1b\\"
    " `try`-`except`-`pass` detected, consider logging the exception\n"
    "   --> sensors/runners/generic.py:247:21\n"
    "    |\n"
    "245 |                               # Process cleanup failed, but we're shutting down anyway\n"
    "246 |                               pass\n"
    "247 | /                     except Exception:\n"
    "248 | |                         # Process already dead or other error, ignore\n"
    "249 | |                         pass\n"
    "    | |____________________________^\n"
    "250 |\n"
    "251 |       async def _run_interval_mode(self) -> None:\n"
    "    |\n"
    "\n"
    "Found 2 errors.\n"
)

RUFF_SECURITY_OUTPUT = """\
S110 `try`-`except`-`pass` detected, consider logging the exception
   --> sensors/runners/generic.py:244:25
    |
242 |                               process.kill()
243 |                               await asyncio.wait_for(process.wait(), timeout=1.0)
244 | /                         except Exception:
245 | |                             # Process cleanup failed, but we're shutting down anyway
246 | |                             pass
    | |________________________________^
247 |                       except Exception:
248 |                           # Process already dead or other error, ignore
    |

S110 `try`-`except`-`pass` detected, consider logging the exception
   --> sensors/runners/generic.py:247:21
    |
245 |                               # Process cleanup failed, but we're shutting down anyway
246 |                               pass
247 | /                     except Exception:
248 | |                         # Process already dead or other error, ignore
249 | |                         pass
    | |____________________________^
250 |
251 |       async def _run_interval_mode(self) -> None:
    |

Found 2 errors.
"""


@pytest.mark.asyncio
async def test_parse_output_failure():
    parser = RuffParser()
    result = await parser.parse_output(RUFF_FAILURE_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 3
    assert len(result.output["violations"]) == 3

    v0 = result.output["violations"][0]
    assert v0["rule"] == "B904"
    assert v0["file"] == "sensors/cli.py"
    assert v0["line"] == 26
    assert v0["column"] == 9

    v1 = result.output["violations"][1]
    assert v1["rule"] == "E501"
    assert v1["file"] == "sensors/config/loader.py"
    assert v1["line"] == 66
    assert v1["column"] == 101

    v2 = result.output["violations"][2]
    assert v2["rule"] == "UP035"
    assert v2["file"] == "sensors/config/schema.py"
    assert v2["line"] == 4
    assert v2["column"] == 1


@pytest.mark.asyncio
async def test_parse_output_success():
    parser = RuffParser()
    result = await parser.parse_output(RUFF_SUCCESS_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = RuffParser()
    result = await parser.parse_output(RUFF_EMPTY_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0


@pytest.mark.asyncio
async def test_parse_output_error():
    parser = RuffParser()
    result = await parser.parse_output("{{{not valid at all")

    assert result.success is True
    assert result.output["errorCount"] == 0


def test_calculate_score():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 5, "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 5
    assert score.direction == "less"


def test_calculate_score_success():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"


def test_format_details_terminal_failure():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 3, "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[red]" in text
    assert "3 issues" in text


def test_format_details_terminal_success():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[green]" in text
    assert "No issues" in text


def test_format_details_html():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 2, "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-error" in html
    assert "2 issues" in html


def test_format_details_html_success():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-success" in html


def test_format_details_llm():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 1, "violations": []},
    )
    assert parser.format_details_llm(result) == "1 issue"


def test_format_failures_success_returns_empty():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "violations": []},
    )
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_terminal():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "E501",
                "message": "Line too long",
                "file": "foo.py",
                "line": 10,
                "column": 101,
            }],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "foo.py:10:101" in text
    assert "E501" in text
    assert "Line too long" in text
    assert "[red]" in text


def test_format_failures_html():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "UP035",
                "message": "deprecated",
                "file": "bar.py",
                "line": 5,
                "column": 1,
            }],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-file" in html
    assert "sensors-rule" in html
    assert "bar.py:5:1" in html
    assert "UP035" in html


def test_format_failures_llm():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "B904",
                "message": "raise from err",
                "file": "cli.py",
                "line": 26,
                "column": 9,
            }],
        },
    )
    text = parser.format_failures_llm(result)
    assert "cli.py:26:9" in text
    assert "B904" in text
    assert "raise from err" in text


@pytest.mark.asyncio
async def test_parse_real_security_output():
    """Test parsing real ruff --select S --output-format full output."""
    parser = RuffParser()
    result = await parser.parse_output(RUFF_SECURITY_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 2
    assert len(result.output["violations"]) == 2

    v0 = result.output["violations"][0]
    assert v0["rule"] == "S110"
    assert v0["file"] == "sensors/runners/generic.py"
    assert v0["line"] == 244
    assert v0["column"] == 25

    v1 = result.output["violations"][1]
    assert v1["rule"] == "S110"
    assert v1["file"] == "sensors/runners/generic.py"
    assert v1["line"] == 247
    assert v1["column"] == 21


@pytest.mark.asyncio
async def test_parse_output_with_ansi_hyperlinks():
    """Regression: ruff emits OSC-8 hyperlinks around rule codes.

    The ST-terminated sequences (\x1b]8;;...\x1b\\) must be stripped
    before parsing, otherwise the violation regex won't match.
    """
    parser = RuffParser()
    stripped = GenericRunner.strip_ansi(RUFF_SECURITY_OUTPUT_WITH_HYPERLINKS)
    result = await parser.parse_output(stripped)

    assert result.success is False
    assert result.output["errorCount"] == 2
    assert len(result.output["violations"]) == 2, (
        f"Expected 2 violations but got {len(result.output['violations'])} — "
        f"ANSI hyperlink stripping is broken"
    )

    v0 = result.output["violations"][0]
    assert v0["rule"] == "S110"
    assert v0["file"] == "sensors/runners/generic.py"
    assert v0["line"] == 244

    v1 = result.output["violations"][1]
    assert v1["rule"] == "S110"
    assert v1["line"] == 247


@pytest.mark.asyncio
async def test_integration_ruff_full_pipeline(tmp_path):
    """Integration test: run ruff on a temp file, strip ANSI, parse — same as the sensors runner does."""
    # Create a Python file with known ruff violations
    bad_file = tmp_path / "bad_code.py"
    bad_file.write_text(
        "from typing import List\n"  # UP035: deprecated typing import
        "import os, sys\n"  # E401: multiple imports on one line
        "x:List[int] = []\n"  # UP006: use list instead of List
    )

    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    process = await asyncio.create_subprocess_exec(
        "uv", "run", "ruff", "check", "--output-format", "full",
        "--select", "UP035,UP006,E401", str(bad_file),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=env,
    )
    stdout, _ = await process.communicate()
    raw = stdout.decode("utf-8", errors="ignore")
    output = GenericRunner.strip_ansi(raw)

    parser = RuffParser()
    result = await parser.parse_output(output)

    error_count = result.output["errorCount"]
    violations = result.output["violations"]

    assert error_count > 0, "Expected ruff to find issues in deliberately bad code"
    assert len(violations) == error_count, (
        f"Violation regex matched {len(violations)} but summary says {error_count} — "
        f"ANSI stripping or regex is broken"
    )

    for v in violations:
        assert "rule" in v
        assert "file" in v
        assert "line" in v
        assert "column" in v

    failures_llm = parser.format_failures_llm(result)
    assert "no details" not in failures_llm, (
        f"format_failures_llm returned '(no details)' despite {len(violations)} violations"
    )


RUFF_JSON_WITH_GUIDANCE = [
    {
        "cell": None,
        "code": "C901",
        "end_location": {"column": 4, "row": 10},
        "filename": "pkg/mod.py",
        "fix": None,
        "location": {"column": 1, "row": 10},
        "message": "`foo` is too complex (12 > 10)",
        "noqa_row": 10,
        "severity": "error",
        "url": "https://docs.astral.sh/ruff/rules/complex-structure",
    },
    {
        GUIDANCE_JSON_KEY: {
            "triggered": ["C901"],
            "rules": {
                "C901": {
                    "short": "Function control flow is too complex",
                    "guidance": "About C901:\nPrefer extracting helpers.",
                },
            },
        },
    },
]


@pytest.mark.asyncio
async def test_parse_output_json_with_guidance():
    parser = RuffParser()
    result = await parser.parse_output(json.dumps(RUFF_JSON_WITH_GUIDANCE))

    assert result.success is False
    assert result.output["errorCount"] == 1
    assert len(result.output["violations"]) == 1
    v = result.output["violations"][0]
    assert v["rule"] == "C901"
    assert v["file"] == "pkg/mod.py"
    assert v["line"] == 10
    assert v["column"] == 1

    block = result.output["ruleGuidance"]
    assert block["triggered"] == ["C901"]
    assert "Prefer extracting helpers." in block["rules"]["C901"]["guidance"]


def test_format_failures_terminal_includes_guidance_at_end():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "C901",
                "message": "too complex",
                "file": "pkg/mod.py",
                "line": 10,
                "column": 1,
            }],
            "ruleGuidance": RUFF_JSON_WITH_GUIDANCE[1][GUIDANCE_JSON_KEY],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "pkg/mod.py:10:1" in text
    assert "Rule guidance:" in text
    assert "About C901:" in text
    assert text.index("pkg/mod.py") < text.index("Rule guidance:")


def test_format_failures_html_attaches_guidance_per_violation():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "C901",
                "message": "too complex",
                "file": "pkg/mod.py",
                "line": 10,
                "column": 1,
            }],
            "ruleGuidance": RUFF_JSON_WITH_GUIDANCE[1][GUIDANCE_JSON_KEY],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-guidance" in html
    assert "About C901:" in html
    assert "Prefer extracting helpers." in html
    assert html.index("sensors-violation") < html.index("sensors-guidance")


def test_format_failures_html_skips_guidance_for_unconfigured_rules():
    parser = RuffParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "violations": [{
                "rule": "E501",
                "message": "line too long",
                "file": "x.py",
                "line": 1,
                "column": 1,
            }],
            "ruleGuidance": {
                "triggered": ["C901"],
                "rules": {
                    "C901": {"short": "complex", "guidance": "refactor"},
                },
            },
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-guidance" not in html
    assert "E501" in html
