from datetime import datetime

import pytest

from sensors.persistence.models import RunnerResult
from sensors.runners.parsers import VitestParser

VITEST_FAILURE_OUTPUT = """\
\x1b[999D\x1b[K

 ❯ src/calculator.test.ts (2)
   ❯ Calculator (2)
     ❯ add (2)
       ✓ should add two positive numbers
       × should add negative numbers

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/calculator.test.ts > Calculator > add > should add negative numbers
AssertionError: expected -5 to be -4 // Object.is equality

- Expected
+ Received

- -4
+ -5

 ❯ src/calculator.test.ts:11:27
      9| 
     10|     it('should add negative numbers', () => {
     11|       expect(add(-2, -3)).toBe(-4);
       |                           ^
     12|     });
     13|   });

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/1]⎯


 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 26 passed (27)
   Start at  13:13:49
   Duration  242ms (transform 30ms, setup 0ms, import 44ms, tests 8ms, environment 0ms)

 FAIL  Tests failed. Watching for file changes...
       press h to show help, press q to quit
"""

VITEST_SUCCESS_OUTPUT = """\
 ✓ src/calculator.test.ts (6)
 ✓ src/calculator.edge-cases.test.ts (21)

 Test Files  2 passed (2)
      Tests  27 passed (27)
   Start at  13:10:00
   Duration  200ms
"""


@pytest.mark.asyncio
async def test_parse_output_success():
    parser = VitestParser()
    result = await parser.parse_output(VITEST_SUCCESS_OUTPUT)

    assert result.success is True
    assert result.output["numPassedTests"] == 27
    assert result.output["numFailedTests"] == 0
    assert result.output["failures"] == []


@pytest.mark.asyncio
async def test_parse_output_failure():
    parser = VitestParser()
    result = await parser.parse_output(VITEST_FAILURE_OUTPUT)

    assert result.success is False
    assert result.output["numPassedTests"] == 26
    assert result.output["numFailedTests"] == 1
    assert len(result.output["failures"]) == 1

    failure = result.output["failures"][0]
    assert failure["type"] == "test_failure"
    assert "src/calculator.test.ts" in failure["test"]
    assert "should add negative numbers" in failure["test"]
    assert "AssertionError" in failure["message"]


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = VitestParser()
    result = await parser.parse_output("")

    assert result.success is True
    assert result.output["numPassedTests"] == 0
    assert result.output["numFailedTests"] == 0
    assert result.output["failures"] == []


@pytest.mark.asyncio
async def test_failure_preserves_full_file_path():
    """The bug: summary[-300:] was truncating file paths."""
    parser = VitestParser()
    result = await parser.parse_output(VITEST_FAILURE_OUTPUT)

    failure = result.output["failures"][0]
    # Full path must be preserved, not truncated like "st.ts"
    assert failure["test"].startswith("src/calculator.test.ts")
    assert "src/calculator.test.ts:11:27" in failure["message"]


@pytest.mark.asyncio
async def test_parse_multiple_failures():
    output = """\
⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯

 FAIL  src/math.test.ts > Math > add > adds numbers
AssertionError: expected 3 to be 4

 FAIL  src/math.test.ts > Math > subtract > subtracts numbers
AssertionError: expected 1 to be 2

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/2]⎯

 Test Files  1 failed (1)
      Tests  2 failed (2)
"""
    parser = VitestParser()
    result = await parser.parse_output(output)

    assert result.output["numFailedTests"] == 2
    assert len(result.output["failures"]) == 2
    assert "adds numbers" in result.output["failures"][0]["test"]
    assert "subtracts numbers" in result.output["failures"][1]["test"]


def test_format_details_success():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"numPassedTests": 5, "numFailedTests": 0, "failures": []},
    )
    assert "5 passed" in parser.format_details_llm(result)


def test_format_details_failure():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"numPassedTests": 3, "numFailedTests": 2, "failures": []},
    )
    assert "2 failed" in parser.format_details_llm(result)
    assert "3 passed" in parser.format_details_llm(result)


def test_format_failures_success():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"numPassedTests": 3, "numFailedTests": 0, "failures": []},
    )
    assert parser.format_failures_llm(result) == ""


def test_format_failures_no_details():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"numPassedTests": 0, "numFailedTests": 2, "failures": []},
    )
    failures = parser.format_failures_llm(result)
    assert "2 tests failed" in failures
    assert "no details available" in failures


def test_format_failures_with_details():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 1,
            "numFailedTests": 1,
            "failures": [
                {
                    "type": "test_failure",
                    "test": "src/calculator.test.ts > Calculator > add > should add negative numbers",
                    "message": "AssertionError: expected -5 to be -4",
                }
            ],
        },
    )
    failures = parser.format_failures_llm(result)
    assert "FAIL:" in failures
    assert "src/calculator.test.ts" in failures
    assert "should add negative numbers" in failures
    assert "AssertionError" in failures


def test_format_failures_html():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 0,
            "numFailedTests": 1,
            "failures": [
                {
                    "type": "test_failure",
                    "test": "src/file.test.ts > Suite > test",
                    "message": "Error msg",
                }
            ],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-error" in html
    assert "sensors-file" in html
    assert "src/file.test.ts" in html
    assert "Suite" in html
    assert "Error msg" in html


VITEST_SUITE_FAILURE_OUTPUT = """\
 ✓  client-browser (chromium)  client/src/components/__tests__/space-selector.test.tsx (8 tests) 775ms
 ✓  client-browser (chromium)  client/src/pages/__tests__/spaces.test.tsx (8 tests) 597ms

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯ Failed Suites 2 ⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯

 FAIL   server  server/__tests__/routes.test.ts [ server/__tests__/routes.test.ts ]
Error: Cannot find module '/server/services/storage-factory' imported from /path/to/routes.test.ts
 ❯ server/__tests__/routes.test.ts:11:1

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[1/2]⎯

 FAIL   server  server/services/__tests__/repository-factory.test.ts [ server/services/__tests__/repository-factory.test.ts ]
Error: Cannot find module '/server/services/storage-factory' imported from /path/to/repository-factory.test.ts
 ❯ server/services/__tests__/repository-factory.test.ts:23:1

⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯[2/2]⎯


 Test Files  2 failed | 16 passed (18)
      Tests  266 passed (266)
   Start at  15:30:57
   Duration  3.09s
"""


@pytest.mark.asyncio
async def test_parse_suite_failure_shows_failed():
    """Suite-level import errors must not be reported as all-green."""
    parser = VitestParser()
    result = await parser.parse_output(VITEST_SUITE_FAILURE_OUTPUT)

    assert result.success is False
    assert result.output["numFailedTests"] > 0
    assert len(result.output["failures"]) == 2
    assert "server/__tests__/routes.test.ts" in result.output["failures"][0]["test"]
    assert "Cannot find module" in result.output["failures"][0]["message"]


def test_is_watch_run_complete():
    parser = VitestParser()
    assert parser.is_watch_run_complete("      Tests  6 passed (6)")
    assert parser.is_watch_run_complete("      Tests  1 failed | 5 passed (6)")
    assert not parser.is_watch_run_complete("FAIL  Tests failed. Watching for file changes...")


def test_calculate_score_failure():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"numPassedTests": 3, "numFailedTests": 2, "failures": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 2
    assert score.direction == "less"


def test_calculate_score_success():
    parser = VitestParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"numPassedTests": 5, "numFailedTests": 0, "failures": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"
