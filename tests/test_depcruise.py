from datetime import datetime

import pytest

from sensors.config import RunnerResult
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


@pytest.mark.asyncio
async def test_parse_output_failure():
    parser = DepcruiseParser()
    result = await parser.parse_output(DEPCRUISE_FAILURE_OUTPUT)

    assert result.success is False
    assert result.output["errorCount"] == 2
    assert result.output["warningCount"] == 0
    assert len(result.output["violations"]) == 2

    v0 = result.output["violations"][0]
    assert v0["severity"] == "error"
    assert v0["rule"] == "use-cases-no-implementations"
    assert v0["source"] == "server/use-cases/_violation-test.ts"
    assert v0["target"] == "server/data-sources/chat/google-chat-client.ts"
    assert "interfaces" in v0["message"]

    v1 = result.output["violations"][1]
    assert v1["severity"] == "error"
    assert v1["rule"] == "domain-no-sdks"
    assert v1["source"] == "server/domain/_violation-test.ts"
    assert v1["target"] == "node_modules/express/index.js"


@pytest.mark.asyncio
async def test_parse_output_success():
    parser = DepcruiseParser()
    result = await parser.parse_output(DEPCRUISE_SUCCESS_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_output_warnings_only():
    parser = DepcruiseParser()
    result = await parser.parse_output(DEPCRUISE_WARNINGS_OUTPUT)

    assert result.success is True  # warnings don't fail
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 1
    assert len(result.output["violations"]) == 1
    assert result.output["violations"][0]["severity"] == "warn"


@pytest.mark.asyncio
async def test_parse_output_empty():
    parser = DepcruiseParser()
    result = await parser.parse_output(DEPCRUISE_EMPTY_OUTPUT)

    assert result.success is True
    assert result.output["errorCount"] == 0
    assert result.output["warningCount"] == 0


@pytest.mark.asyncio
async def test_parse_output_garbage():
    parser = DepcruiseParser()
    result = await parser.parse_output("{{{not valid at all")

    assert result.success is True
    assert result.output["errorCount"] == 0


def test_calculate_score():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 2, "warningCount": 1, "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 3
    assert score.direction == "less"


def test_calculate_score_success():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "warningCount": 0, "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"


def test_format_details_terminal_failure():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 2, "warningCount": 0, "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[red]" in text
    assert "2 errors" in text


def test_format_details_terminal_warnings():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "warningCount": 3, "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[yellow]" in text
    assert "3 warnings" in text


def test_format_details_terminal_success():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "warningCount": 0, "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[green]" in text
    assert "No violations" in text


def test_format_details_html():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 2, "warningCount": 0, "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-error" in html
    assert "2 errors" in html


def test_format_details_html_success():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "warningCount": 0, "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-success" in html


def test_format_details_llm():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"errorCount": 1, "warningCount": 0, "violations": []},
    )
    assert parser.format_details_llm(result) == "1 error"


def test_format_failures_success_returns_empty():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"errorCount": 0, "warningCount": 0, "violations": []},
    )
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_terminal():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "warningCount": 0,
            "violations": [{
                "severity": "error",
                "rule": "no-circular",
                "source": "src/a.ts",
                "target": "src/b.ts",
                "message": "Circular dependency detected.",
            }],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "src/a.ts" in text
    assert "src/b.ts" in text
    assert "no-circular" in text
    assert "[red]" in text
    assert "Circular dependency" in text


def test_format_failures_html():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "warningCount": 0,
            "violations": [{
                "severity": "error",
                "rule": "no-circular",
                "source": "src/a.ts",
                "target": "src/b.ts",
                "message": "Circular dependency detected.",
            }],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-file" in html
    assert "sensors-rule" in html
    assert "no-circular" in html
    assert "src/a.ts" in html


def test_format_failures_llm():
    parser = DepcruiseParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "errorCount": 1,
            "warningCount": 0,
            "violations": [{
                "severity": "error",
                "rule": "domain-no-sdks",
                "source": "server/domain/x.ts",
                "target": "node_modules/express/index.js",
                "message": "Domain must not import SDKs.",
            }],
        },
    )
    text = parser.format_failures_llm(result)
    assert "domain-no-sdks" in text
    assert "server/domain/x.ts" in text
    assert "node_modules/express/index.js" in text
    assert "Domain must not import SDKs." in text
