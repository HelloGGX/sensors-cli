from sensors.runners.parsers.pytest import PytestParser


def _metric(parsed, key):
    return next(m.value for m in parsed.metrics if m.key == key)


def test_parse_passed():
    parser = PytestParser()
    output = """\
============================= test session starts ==============================
collected 3 items

foo.py::test_one PASSED
foo.py::test_two PASSED
foo.py::test_three PASSED

========================== 3 passed in 0.12s ==========================
"""

    parsed = parser.parse(output)

    assert parsed.success is True
    assert parsed.summary == "3 passed"
    assert _metric(parsed, "passed") == 3
    assert _metric(parsed, "failed") == 0
    assert _metric(parsed, "errors") == 0


def test_parse_failures_and_collection_error():
    parser = PytestParser()
    output = """\
============================= test session starts ==============================
collected 2 items / 1 error

==================================== ERRORS ====================================
ERROR collecting tests/test_bad.py
ImportError while importing test module

=================================== FAILURES ===================================
_________________________ test_two _________________________

    def test_two():
>       assert False
E       AssertionError

========================== 1 failed in 0.12s ==========================
"""

    parsed = parser.parse(output)

    assert parsed.success is False
    assert _metric(parsed, "failed") == 1
    assert _metric(parsed, "errors") == 1
    assert len(parsed.findings) == 2
    assert {f.rule for f in parsed.findings} == {"test_failure", "collection_error"}


def test_parse_empty():
    parser = PytestParser()
    parsed = parser.parse("")

    assert parsed.success is True
    assert parsed.summary == "0 passed"
    assert parsed.findings == []
