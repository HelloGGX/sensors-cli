from sensors.runners.parsers import VitestParser

VITEST_FAILURE_OUTPUT = """\
⎯⎯⎯ Failed Tests 1 ⎯⎯⎯

 FAIL  src/calculator.test.ts > Calculator > add > should add negative numbers
AssertionError: expected -5 to be -4

[1/1]

 Test Files  1 failed | 1 passed (2)
      Tests  1 failed | 26 passed (27)
"""

VITEST_SUITE_FAILURE_OUTPUT = """\
⎯⎯⎯ Failed Suites 2 ⎯⎯⎯

 FAIL   server  server/__tests__/routes.test.ts [ server/__tests__/routes.test.ts ]
Error: Cannot find module '/server/services/storage-factory'

 FAIL   server  server/services/__tests__/repository-factory.test.ts [ server/services/__tests__/repository-factory.test.ts ]
Error: Cannot find module '/server/services/storage-factory'

[2/2]

 Test Files  2 failed | 16 passed (18)
      Tests  266 passed (266)
"""


def _metric(parsed, key):
    return next(m.value for m in parsed.metrics if m.key == key)


def test_parse_success():
    parser = VitestParser()
    parsed = parser.parse("Tests  27 passed (27)")

    assert parsed.success is True
    assert parsed.summary == "27 passed"
    assert _metric(parsed, "passed") == 27
    assert _metric(parsed, "failed") == 0


def test_parse_failure():
    parser = VitestParser()
    parsed = parser.parse(VITEST_FAILURE_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "1 failed, 26 passed"
    assert _metric(parsed, "failed") == 1
    assert len(parsed.findings) == 1
    assert parsed.findings[0].file == "src/calculator.test.ts"
    assert "AssertionError" in parsed.findings[0].message


def test_parse_suite_failure_uses_test_files_line():
    parser = VitestParser()
    parsed = parser.parse(VITEST_SUITE_FAILURE_OUTPUT)

    assert parsed.success is False
    assert _metric(parsed, "failed") == 2
    assert len(parsed.findings) == 2


def test_watch_completion_detection():
    parser = VitestParser()
    assert parser.is_watch_run_complete("      Tests  6 passed (6)")
    assert parser.is_watch_run_complete("      Tests  1 failed | 5 passed (6)")
    assert not parser.is_watch_run_complete("FAIL  Tests failed. Watching for file changes...")
