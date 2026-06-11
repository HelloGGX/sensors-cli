from sensors.runners.parsers.pytest_cov import PytestCovParser

SAMPLE_OUTPUT = """\
===================================== tests coverage =====================================

Name                      Stmts   Miss  Cover   Missing
-------------------------------------------------------
sensors/cli.py               17     17     0%   3-35
sensors/config/loader.py     58     48    17%   32-70
-------------------------------------------------------
TOTAL                        75     65    13%
======================= 5 passed, 2 skipped in 2.66s ========================
"""

SAMPLE_OUTPUT_FAIL = """\
Name        Stmts   Miss  Cover
-------------------------------
app/main.py    50     10    80%
-------------------------------
TOTAL          50     10    80%

FAIL Required test coverage of 90%. Got 80%
========================== 5 passed in 1.00s ==========================
"""


def _metric(parsed, key):
    return next(m.value for m in parsed.metrics if m.key == key)


def test_parse_success():
    parser = PytestCovParser()
    parsed = parser.parse(SAMPLE_OUTPUT)

    assert parsed.success is True
    assert parsed.summary == "13% coverage, 5 passed"
    assert parsed.score.value == 13
    assert _metric(parsed, "passed") == 5
    assert _metric(parsed, "failed") == 0
    assert _metric(parsed, "misses") == 65
    assert len(parsed.extra["files"]) == 2


def test_parse_cov_fail_under():
    parser = PytestCovParser()
    parsed = parser.parse(SAMPLE_OUTPUT_FAIL)

    assert parsed.success is False
    assert parsed.score.value == 80
    assert "FAIL Required test coverage" in (parsed.extra.get("coverageFailure") or "")


def test_parse_empty():
    parser = PytestCovParser()
    parsed = parser.parse("")

    assert parsed.success is True
    assert parsed.summary == "0% coverage, 0 passed"
