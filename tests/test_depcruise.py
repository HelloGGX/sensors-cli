from sensors.runners.parsers.depcruise import DepcruiseParser

DEPCRUISE_FAILURE_OUTPUT = """\

> rest-express@1.0.0 lint:deps
> depcruise server/ client/src/ shared/ --config --output-type err-long


  error use-cases-no-implementations: server/use-cases/_violation-test.ts → server/data-sources/chat/google-chat-client.ts
    Use-cases must depend on data source interfaces, not implementations.
    Import the interface file (e.g. chat-data-source.ts) and let the
    composition root wire in the concrete implementation. (Principle 1:
    Pluggable data sources)

  error domain-no-sdks: server/domain/_violation-test.ts → node_modules/express/index.js
    Domain logic must not import external SDKs. Domain should be pure
    computation with no infrastructure dependencies. Import interfaces from
    data-sources instead. (Principle 1: Pluggable data sources)


x 2 dependency violations (2 errors, 0 warnings). 129 modules, 302 dependencies cruised.

"""

DEPCRUISE_SUCCESS_OUTPUT = """\

> rest-express@1.0.0 lint:deps
> depcruise server/ client/src/ shared/ --config --output-type err-long


✔ 0 dependency violations (0 errors, 0 warnings). 129 modules, 302 dependencies cruised.

"""

DEPCRUISE_WARNINGS_OUTPUT = """\
  warn optional-dep-missing: server/utils/cache.ts → node_modules/ioredis/built/index.js
    Optional dependency not installed. Consider adding it to optionalDependencies.

x 1 dependency violations (0 errors, 1 warnings). 50 modules, 80 dependencies cruised.
"""

DEPCRUISE_EMPTY_OUTPUT = ""


def test_parse_failure():
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_FAILURE_OUTPUT)

    assert parsed.success is False
    assert len(parsed.findings) == 2

    f0 = parsed.findings[0]
    assert "server/use-cases/_violation-test.ts" in f0.file
    assert "server/data-sources/chat/google-chat-client.ts" in f0.file
    assert f0.rule == "use-cases-no-implementations"
    assert f0.severity == "error"
    assert "interfaces" in f0.message

    f1 = parsed.findings[1]
    assert "server/domain/_violation-test.ts" in f1.file
    assert f1.rule == "domain-no-sdks"
    assert f1.severity == "error"

    assert parsed.score.value == 2
    assert parsed.score.direction == "less"
    assert "2 errors" in parsed.summary


def test_parse_success():
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_SUCCESS_OUTPUT)

    assert parsed.success is True
    assert parsed.findings == []
    assert parsed.score.value == 0
    assert parsed.summary == "No violations"


def test_parse_warnings_only():
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_WARNINGS_OUTPUT)

    assert parsed.success is True  # warnings don't fail
    assert len(parsed.findings) == 1
    assert parsed.findings[0].severity == "warning"
    assert parsed.score.value == 1


def test_parse_empty():
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_EMPTY_OUTPUT)

    assert parsed.success is True
    assert parsed.findings == []
    assert parsed.score.value == 0


def test_parse_garbage():
    parser = DepcruiseParser()
    parsed = parser.parse("{{{not valid at all")

    assert parsed.success is True
    assert parsed.findings == []


def test_parse_metrics():
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_FAILURE_OUTPUT)

    ec = next(m for m in parsed.metrics if m.key == "errorCount")
    wc = next(m for m in parsed.metrics if m.key == "warningCount")
    assert ec.value == 2
    assert wc.value == 0


def test_parse_violation_edge_format():
    """Finding.file contains the source → target edge."""
    parser = DepcruiseParser()
    parsed = parser.parse(DEPCRUISE_FAILURE_OUTPUT)

    f = parsed.findings[0]
    assert "→" in f.file
    assert f.file == "server/use-cases/_violation-test.ts → server/data-sources/chat/google-chat-client.ts"

