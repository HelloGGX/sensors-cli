from sensors.runners.parsers.import_linter import ImportLinterParser


FAILURE_OUTPUT = """\
---------
Contracts
---------
Layer hierarchy KEPT
TUI must not import StateManager BROKEN

Contracts: 1 kept, 1 broken.

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
Layer hierarchy KEPT
TUI must not import StateManager KEPT

Contracts: 2 kept, 0 broken.
"""


def test_parse_failure():
    parser = ImportLinterParser()
    parsed = parser.parse(FAILURE_OUTPUT)

    assert parsed.success is False
    assert parsed.summary == "1 broken, 1 kept"
    assert parsed.score.value == 1
    assert len(parsed.findings) == 1

    finding = parsed.findings[0]
    assert finding.rule == "TUI must not import StateManager"
    assert finding.file == "sensors.tui.display -> sensors.persistence.state_manager"
    assert finding.line == 19
    assert "not allowed" in (finding.context or "")


def test_parse_success():
    parser = ImportLinterParser()
    parsed = parser.parse(SUCCESS_OUTPUT)

    assert parsed.success is True
    assert parsed.summary == "All 2 contracts kept"
    assert parsed.findings == []


def test_parse_without_summary_uses_fallback_counts():
    parser = ImportLinterParser()
    parsed = parser.parse("""\
Broken contracts

My Contract
-----------

a.b is not allowed to import c.d:

-   a.b -> c.d
""")

    assert parsed.success is False
    assert parsed.score.value == 1
    assert len(parsed.findings) == 1
