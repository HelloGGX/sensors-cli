# Skill: Create a New Sensors Parser

## When to Use

Use this skill when the user asks to add support for a new tool output in sensors, for example:
- add parser for tool X
- support command Y output
- monitor Z with sensors

## Overview

The sensors system uses a plugin architecture:
- GenericRunner handles process execution and orchestration.
- Parsers transform raw output into structured SensorReading.
- GenericFormatter renders SensorReading for terminal, HTML, and LLM views.

## Step-by-step Instructions

### 1. Read the Current Interface

Read these files first:
- sensors/runners/parsers/base.py
- sensors/config/result_types.py
- sensors/runners/formatter.py

Do not assume legacy methods exist. The parser contract is parse(output: str) -> SensorReading.

### 2. Collect Real Sample Output

Ask for real command output or a sample file. Parsing should be based on actual tool output, not guessed patterns.

### 3. Implement the Parser

Create sensors/runners/parsers/<parser_name>.py.

Implement:
- class <ParserClass>(OutputParser)
- def parse(self, output: str) -> SensorReading

Design rules:
- Keep parsing logic small and composable (extract helpers for sections).
- Return structured fields:
  - success
  - label
  - score (ScoreInfo)
  - findings (Finding)
  - metrics (Metric)
  - guidance (GuidanceBlock) if applicable
  - extra for tool-specific data that does not fit generic structures
- Handle parse failures gracefully by returning SensorReading with:
  - success=False
  - label set to a parse error message
  - score set to a safe default
  - extra containing parseError and optional raw excerpt
- The runner strips ANSI before parsing. Regex should target plain text.

### 4. Write Tests

Create tests/test_<parser_name>.py with parse-focused tests.

Minimum coverage:
- success sample
- failure sample
- edge case sample (empty or malformed input)
- watch completion behavior (if parser implements is_watch_run_complete)

Test style:
- call parser.parse(output)
- assert SensorReading fields (label, score, findings, metrics, extra)
- avoid testing formatting methods in parser tests

Run:
- uv run pytest tests/test_<parser_name>.py -v

### 5. Register the Parser

Edit sensors/runners/parsers/__init__.py:
- import the parser class
- add ParserRegistry.register("<parser_name>", <ParserClass>)

### 6. Add Config Example

Add or update a runner entry in config/default.sensors.yaml:

- name: runner-name
  parser: parser-name
  enabled: true
  mode: watch
  command: tool command

For interval mode add interval in ms.
Set workingDir only when needed.

### 7. Validate End-to-end

Run:
- sensors check .

If parser registration changed, reinstall tool if needed:
- uv tool install . --force --reinstall

## Naming

- Parser name: lowercase snake_case (example: pytest_cov)
- Class name: PascalCase with Parser suffix (example: PytestCovParser)
- File: sensors/runners/parsers/<parser_name>.py
- Test: tests/test_<parser_name>.py
