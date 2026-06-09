import asyncio
import os
from datetime import datetime

import pytest

from sensors.config import RunnerResult
from sensors.runners.generic import GenericRunner
from sensors.runners.parsers.import_linter import ImportLinterParser

FAILURE_OUTPUT = """\

╔══╗─────────▶╔╗ ╔╗      ╔╗◀───┐
╚╣╠╝◀─────┐  ╔╝╚╗║║────▶╔╝╚╗   │
 ║║   ╔══╦══╦╩╗╔╝║║  ╔╦═╩╗╔╝╔═╦══╗
 ║║╔══╣╔╗║╔╗║╔╣║ ║║ ╔╬╣╔╗║║ ║│║╔═╝
╔╣╠╣║║║╚╝║╚╝║║║╚╗║╚═╝║║║║║╚╗║═╣║
╚══╩╩╩╣╔═╩══╩╝╚═╝╚═══╩╩╝╚╩═╩╩═╩╝
  └──▶║║                    ▲ 
      ╚╝────────────────────┘

---------
Contracts
---------

Analyzed 26 files, 54 dependencies.
-----------------------------------

Layer hierarchy KEPT
Parsers must not import GenericRunner KEPT
TUI must not import StateManager BROKEN

Contracts: 2 kept, 1 broken.


----------------
Broken contracts
----------------

TUI must not import StateManager
--------------------------------

sensors.tui is not allowed to import sensors.persistence.state_manager:

-   sensors.tui.display -> sensors.persistence.state_manager (l.19)

"""

SUCCESS_OUTPUT = """\

---------
Contracts
---------

Analyzed 26 files, 54 dependencies.
-----------------------------------

Layer hierarchy KEPT
Parsers must not import GenericRunner KEPT
TUI must not import StateManager KEPT

Contracts: 3 kept, 0 broken.

"""

MULTIPLE_BROKEN_OUTPUT = """\

---------
Contracts
---------

Analyzed 26 files, 54 dependencies.
-----------------------------------

Layer hierarchy BROKEN
Parsers must not import GenericRunner BROKEN
TUI must not import StateManager KEPT

Contracts: 1 kept, 2 broken.


----------------
Broken contracts
----------------

Layer hierarchy
---------------

sensors.persistence is not allowed to import sensors.runners:

-   sensors.persistence.state_manager -> sensors.runners.generic (l.5)
-   sensors.persistence.models -> sensors.runners.parsers (l.3)

Parsers must not import GenericRunner
--------------------------------------

sensors.runners.parsers is not allowed to import sensors.runners.generic:

-   sensors.runners.parsers.pytest -> sensors.runners.generic (l.7)

"""


@pytest.mark.asyncio
async def test_parse_failure():
    parser = ImportLinterParser()
    result = await parser.parse_output(FAILURE_OUTPUT)

    assert result.success is False
    assert result.output["keptCount"] == 2
    assert result.output["brokenCount"] == 1
    assert len(result.output["violations"]) == 1

    v = result.output["violations"][0]
    assert v["contract"] == "TUI must not import StateManager"
    assert "state_manager" in v["description"]
    assert v["source"] == "sensors.tui.display"
    assert v["target"] == "sensors.persistence.state_manager"
    assert v["line"] == "19"


@pytest.mark.asyncio
async def test_parse_success():
    parser = ImportLinterParser()
    result = await parser.parse_output(SUCCESS_OUTPUT)

    assert result.success is True
    assert result.output["keptCount"] == 3
    assert result.output["brokenCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_multiple_broken():
    parser = ImportLinterParser()
    result = await parser.parse_output(MULTIPLE_BROKEN_OUTPUT)

    assert result.success is False
    assert result.output["brokenCount"] == 2
    assert result.output["keptCount"] == 1
    assert len(result.output["violations"]) == 3

    contracts_in_violations = {v["contract"] for v in result.output["violations"]}
    assert "Layer hierarchy" in contracts_in_violations
    assert "Parsers must not import GenericRunner" in contracts_in_violations


@pytest.mark.asyncio
async def test_parse_empty():
    parser = ImportLinterParser()
    result = await parser.parse_output("")

    assert result.success is True
    assert result.output["brokenCount"] == 0
    assert result.output["violations"] == []


@pytest.mark.asyncio
async def test_parse_garbage():
    parser = ImportLinterParser()
    result = await parser.parse_output("{{{ not valid output !!!}")

    assert result.success is True
    assert result.output["brokenCount"] == 0


def test_calculate_score():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"keptCount": 2, "brokenCount": 1, "contracts": [], "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 1
    assert score.direction == "less"


def test_calculate_score_success():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"keptCount": 3, "brokenCount": 0, "contracts": [], "violations": []},
    )
    score = parser.calculate_score(result)
    assert score.value == 0
    assert score.direction == "less"


def test_format_details_terminal_failure():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"keptCount": 2, "brokenCount": 1, "contracts": [], "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[red]" in text
    assert "1 broken" in text


def test_format_details_terminal_success():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"keptCount": 3, "brokenCount": 0, "contracts": [], "violations": []},
    )
    text = parser.format_details_terminal(result)
    assert "[green]" in text
    assert "All 3 contracts kept" in text


def test_format_details_html_failure():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"keptCount": 2, "brokenCount": 1, "contracts": [], "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-error" in html
    assert "1 broken" in html


def test_format_details_html_success():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"keptCount": 3, "brokenCount": 0, "contracts": [], "violations": []},
    )
    html = parser.format_details_html(result)
    assert "sensors-success" in html


def test_format_details_llm():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={"keptCount": 2, "brokenCount": 1, "contracts": [], "violations": []},
    )
    text = parser.format_details_llm(result)
    assert "<" not in text
    assert "1 broken" in text


def test_format_failures_success_returns_empty():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=True,
        output={"keptCount": 3, "brokenCount": 0, "contracts": [], "violations": []},
    )
    assert parser.format_failures_terminal(result) == ""
    assert parser.format_failures_html(result) == ""
    assert parser.format_failures_llm(result) == ""


def test_format_failures_terminal():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "keptCount": 2,
            "brokenCount": 1,
            "contracts": [],
            "violations": [{
                "contract": "TUI must not import StateManager",
                "description": "sensors.tui is not allowed to import sensors.persistence.state_manager",
                "source": "sensors.tui.display",
                "target": "sensors.persistence.state_manager",
                "line": "19",
            }],
        },
    )
    text = parser.format_failures_terminal(result)
    assert "TUI must not import StateManager" in text
    assert "sensors.tui.display" in text
    assert "sensors.persistence.state_manager" in text
    assert "(l.19)" in text
    assert "[red]" in text


def test_format_failures_html():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "keptCount": 2,
            "brokenCount": 1,
            "contracts": [],
            "violations": [{
                "contract": "TUI must not import StateManager",
                "description": "sensors.tui is not allowed to import sensors.persistence.state_manager",
                "source": "sensors.tui.display",
                "target": "sensors.persistence.state_manager",
                "line": "19",
            }],
        },
    )
    html = parser.format_failures_html(result)
    assert "sensors-violation" in html
    assert "sensors-rule" in html
    assert "sensors-error" in html
    assert "TUI must not import StateManager" in html
    assert "sensors.tui.display" in html


def test_format_failures_llm():
    parser = ImportLinterParser()
    result = RunnerResult(
        timestamp=datetime.utcnow(),
        success=False,
        output={
            "keptCount": 2,
            "brokenCount": 1,
            "contracts": [],
            "violations": [{
                "contract": "TUI must not import StateManager",
                "description": "sensors.tui is not allowed to import sensors.persistence.state_manager",
                "source": "sensors.tui.display",
                "target": "sensors.persistence.state_manager",
                "line": "19",
            }],
        },
    )
    text = parser.format_failures_llm(result)
    assert "<" not in text
    assert "BROKEN: TUI must not import StateManager" in text
    assert "sensors.tui.display -> sensors.persistence.state_manager" in text
    assert "(l.19)" in text


@pytest.mark.asyncio
async def test_integration_full_pipeline():
    """Run the real lint-imports command, strip ANSI, parse — same as the sensors runner."""
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    process = await asyncio.create_subprocess_shell(
        "uv run lint-imports",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
    )
    stdout, _ = await process.communicate()
    output = GenericRunner.strip_ansi(stdout.decode("utf-8", errors="ignore"))

    parser = ImportLinterParser()
    result = await parser.parse_output(output)

    summary_match = result.output.get("keptCount", 0) + result.output.get("brokenCount", 0)
    assert summary_match > 0, "Expected at least one contract to be parsed"
    assert len(result.output["violations"]) == sum(
        1 for v in result.output["violations"]
    ), "violations list should be consistent"
    assert result.output["brokenCount"] == len(
        {v["contract"] for v in result.output["violations"]}
    ), "brokenCount should match unique broken contracts in violations"
