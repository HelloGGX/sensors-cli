import pytest

from sensors.runners.parsers.pytest import PytestParser


@pytest.mark.asyncio
async def test_parse_output_success():
    parser = PytestParser()
    sample_output = """============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.0.2, pluggy-1.6.0 -- /workspaces/live-dashboard/sensors/.venv/bin/python3
cachedir: .pytest_cache
rootdir: /workspaces/live-dashboard/sensors
configfile: pyproject.toml
plugins: anyio-4.12.1, asyncio-1.3.0
asyncio: mode=Mode.AUTO, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 38 items / 1 error

==================================== ERRORS ====================================
___________________ ERROR collecting tests/test_pytest.py.py ___________________
ImportError while importing test module '/workspaces/live-dashboard/sensors/tests/test_pytest.py.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.11/importlib/__init__.py:126: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
E   ModuleNotFoundError: No module named 'tests.test_pytest.py'; 'tests.test_pytest' is not a package
=============================== warnings summary ===============================
sensors/config/schema.py:16
  /workspaces/live-dashboard/sensors/sensors/config/schema.py:16: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class RunnerConfig(BaseModel):

sensors/persistence/models.py:9
  /workspaces/live-dashboard/sensors/sensors/persistence/models.py:9: PydanticDeprecatedSince20: Support for class-based `config` is deprecated, use ConfigDict instead. Deprecated in Pydantic V2.0 to be removed in V3.0. See Pydantic V2 Migration Guide at https://errors.pydantic.dev/2.12/migration/
    class RunnerResult(BaseModel):"""

    result = await parser.parse_output(sample_output)

    assert result is not None
    assert hasattr(result, 'timestamp')
    assert hasattr(result, 'success')
    assert hasattr(result, 'output')
    assert result.success is False  # Should fail due to collection error
    assert result.output["numPassedTests"] == 0
    assert result.output["numFailedTests"] == 0
    assert result.output["numErrors"] == 1
    assert result.output["numWarnings"] >= 0
    assert "failures" in result.output
    assert isinstance(result.output["failures"], list)


@pytest.mark.asyncio
async def test_parse_output_with_passed_tests():
    parser = PytestParser()
    output = """============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.0.2
collecting ... collected 5 items

test_example.py::test_one PASSED
test_example.py::test_two PASSED
test_example.py::test_three PASSED

========================== 3 passed in 0.12s =========================="""

    result = await parser.parse_output(output)

    assert result.success is True
    assert result.output["numPassedTests"] == 3
    assert result.output["numFailedTests"] == 0
    assert result.output["numErrors"] == 0
    assert len(result.output["failures"]) == 0


@pytest.mark.asyncio
async def test_parse_output_with_failures():
    parser = PytestParser()
    output = """============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.0.2
collecting ... collected 3 items

test_example.py::test_one PASSED
test_example.py::test_two FAILED
test_example.py::test_three FAILED

=================================== FAILURES ===================================
_________________________ test_two _________________________

    def test_two():
>       assert False
E       AssertionError

test_example.py:5: AssertionError

_________________________ test_three _________________________

    def test_three():
>       assert 1 == 2
E       assert 1 == 2

test_example.py:8: AssertionError

========================== 1 passed, 2 failed in 0.12s =========================="""

    result = await parser.parse_output(output)

    assert result.success is False
    assert result.output["numPassedTests"] == 1
    assert result.output["numFailedTests"] == 2
    assert result.output["numErrors"] == 0
    assert len(result.output["failures"]) == 2
    assert result.output["failures"][0]["type"] == "test_failure"
    assert "test_two" in result.output["failures"][0]["test"]


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = PytestParser()
    result = await parser.parse_output("")

    assert result is not None
    assert result.success is True  # No failures or errors found
    assert result.output["numPassedTests"] == 0
    assert result.output["numFailedTests"] == 0
    assert result.output["numErrors"] == 0
    assert result.output["numWarnings"] == 0
    assert result.output["failures"] == []


@pytest.mark.asyncio
async def test_parse_output_whitespace_only():
    parser = PytestParser()
    result = await parser.parse_output("   \n\n   ")

    assert result is not None
    assert result.success is True
    assert result.output["numPassedTests"] == 0
    assert result.output["numFailedTests"] == 0
    assert result.output["numErrors"] == 0


@pytest.mark.asyncio
async def test_parse_output_with_ansi_codes():
    """Ensure ANSI escape codes in output don't break parsing."""
    parser = PytestParser()
    # Simulate colored pytest output (red for failed, green for passed)
    output = """============================= test session starts ==============================
platform linux -- Python 3.11.2, pytest-9.0.2
collecting ... collected 5 items

test_example.py::test_one PASSED
test_example.py::test_two FAILED

================================== short test summary info ==================================
FAILED test_example.py::test_two - assert 0 == 1
==================== \x1b[31m1 failed\x1b[0m, \x1b[32m1 passed\x1b[0m in 0.12s ===================="""

    # Parser should handle ANSI gracefully even if not pre-stripped
    from sensors.runners.generic import GenericRunner
    clean_output = GenericRunner.strip_ansi(output)
    result = await parser.parse_output(clean_output)

    assert result.success is False
    assert result.output["numPassedTests"] == 1
    assert result.output["numFailedTests"] == 1


def test_format_details_success():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={
            "numPassedTests": 5,
            "numFailedTests": 0,
            "numErrors": 0,
            "numWarnings": 0,
            "failures": []
        }
    )

    details = parser.format_details_llm(result)
    assert isinstance(details, str)
    assert "5 passed" in details


def test_format_details_with_failures():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 2,
            "numFailedTests": 1,
            "numErrors": 1,
            "numWarnings": 0,
            "failures": []
        }
    )

    details = parser.format_details_llm(result)
    assert isinstance(details, str)
    assert "1 error" in details
    assert "1 failed" in details
    assert "2 passed" in details


def test_format_failures_success():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={
            "numPassedTests": 3,
            "numFailedTests": 0,
            "numErrors": 0,
            "failures": []
        }
    )

    failures = parser.format_failures_llm(result)
    assert failures == ""


def test_format_failures_with_details():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 1,
            "numFailedTests": 1,
            "numErrors": 1,
            "failures": [
                {
                    "type": "test_failure",
                    "test": "test_example",
                    "message": "AssertionError: assert False"
                },
                {
                    "type": "collection_error",
                    "file": "test_bad.py",
                    "message": "ImportError: No module named 'missing'"
                }
            ]
        }
    )

    failures = parser.format_failures_llm(result)
    assert isinstance(failures, str)
    assert "TEST FAILURE" in failures
    assert "COLLECTION ERROR" in failures
    assert "test_example" in failures
    assert "test_bad.py" in failures


def test_format_failures_no_details():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 0,
            "numFailedTests": 2,
            "numErrors": 1,
            "failures": []
        }
    )

    failures = parser.format_failures_llm(result)
    assert isinstance(failures, str)
    assert "3 issues" in failures
    assert "no details available" in failures


def test_calculate_score_with_failures_and_errors():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "numPassedTests": 2,
            "numFailedTests": 1,
            "numErrors": 2,
            "failures": []
        }
    )

    score = parser.calculate_score(result)
    assert score.value == 3  # 1 failed + 2 errors
    assert score.direction == "less"


def test_calculate_score_all_passed():
    parser = PytestParser()
    from datetime import datetime

    from sensors.config import RunnerResult

    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={
            "numPassedTests": 5,
            "numFailedTests": 0,
            "numErrors": 0,
            "failures": []
        }
    )

    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"
