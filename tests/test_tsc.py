"""Tests for TscParser parse() and watch completion detection."""

from sensors.runners.parsers.tsc import TscParser

TSC_MULTI_ERROR_OUTPUT = """\

> rest-express@1.0.0 typecheck
> tsc

server/services/chat-analytics.ts:91:26 - error TS2802: Type 'Set<string>' can only be iterated through when using the '--downlevelIteration' flag or with a '--target' of 'es2015' or higher.

server/services/mock-repository.ts:92:5 - error TS2739: Type '{ a: number; }' is missing the following properties from type '{ a: number; b: string; }': b

Found 2 errors in 2 files.
"""

TSC_SINGLE_FILE_OUTPUT = """\
src/index.ts:10:3 - error TS2322: Type 'string' is not assignable to type 'number'.

Found 1 error.
"""

TSC_CLEAN_OUTPUT = ""

TSC_WATCH_COMPLETE_LINE = "[12:00:01 AM] Found 2 errors. Watching for file changes."
TSC_WATCH_CLEAN_LINE = "[12:00:01 AM] Found 0 errors. Watching for file changes."

TSC_PAREN_FORMAT_OUTPUT = """\
src/foo.ts(5,10): error TS1005: ';' expected.

Found 1 error.
"""


def test_parse_multi_error():
    parser = TscParser()
    parsed = parser.parse(TSC_MULTI_ERROR_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "2 errors in 2 files"
    assert parsed.score.value == 2

    error_count = next(m for m in parsed.metrics if m.key == "errorCount")
    file_count = next(m for m in parsed.metrics if m.key == "fileCount")
    assert error_count.value == 2
    assert file_count.value == 2
    assert len(parsed.findings) == 2


def test_parse_multi_error_fields():
    parser = TscParser()
    parsed = parser.parse(TSC_MULTI_ERROR_OUTPUT)

    f0 = parsed.findings[0]
    assert f0.file == "server/services/chat-analytics.ts"
    assert f0.line == 91
    assert f0.column == 26
    assert f0.rule == "TS2802"
    assert "downlevelIteration" in f0.message
    assert f0.severity == "error"

    f1 = parsed.findings[1]
    assert f1.file == "server/services/mock-repository.ts"
    assert f1.rule == "TS2739"


def test_parse_single_file_error():
    parser = TscParser()
    parsed = parser.parse(TSC_SINGLE_FILE_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "1 error"
    error_count = next(m for m in parsed.metrics if m.key == "errorCount")
    file_count = next(m for m in parsed.metrics if m.key == "fileCount")
    assert error_count.value == 1
    assert file_count.value == 1
    assert parsed.findings[0].rule == "TS2322"


def test_parse_clean_output():
    parser = TscParser()
    parsed = parser.parse(TSC_CLEAN_OUTPUT)

    assert parsed.success is True
    assert parsed.summary == "No errors"
    assert parsed.findings == []
    assert parsed.score.value == 0


def test_parse_paren_format():
    """tsc can emit file(line,col) format depending on config."""
    parser = TscParser()
    parsed = parser.parse(TSC_PAREN_FORMAT_OUTPUT)

    assert parsed.success is False
    assert len(parsed.findings) == 1
    finding = parsed.findings[0]
    assert finding.file == "src/foo.ts"
    assert finding.line == 5
    assert finding.column == 10
    assert finding.rule == "TS1005"


def test_watch_complete_with_errors():
    parser = TscParser()
    assert parser.is_watch_run_complete(TSC_WATCH_COMPLETE_LINE) is True


def test_watch_complete_clean():
    parser = TscParser()
    assert parser.is_watch_run_complete(TSC_WATCH_CLEAN_LINE) is True


def test_watch_not_complete():
    parser = TscParser()
    assert parser.is_watch_run_complete("error TS2322: something") is False
