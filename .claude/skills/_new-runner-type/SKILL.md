# Skill: Create a New Sensors Runner Type (Output Parser)

## When to Use

Use this skill when the user asks to add a new runner type, output parser, or monitoring target to the sensors system. This covers requests like "add support for X tool", "create a parser for Y output", or "monitor Z command".

## Overview

The sensors system uses a plugin architecture: a `GenericRunner` handles all process management (spawning, watching, intervals), and **output parsers** handle tool-specific parsing and formatting. Each parser is a stateless Python class that subclasses `OutputParser`.

## Step-by-step Instructions

### 1. Understand the Interface

Read `sensors/runners/parsers/base.py` — this is the source of truth for the `OutputParser` abstract base class. Do NOT guess the interface; always read the file.

The parser also uses `RunnerResult` and `ScoreInfo` defined in `sensors/persistence/models.py`, read that as well.

### 2. Get a Sample Output

Ask the user for a sample output from the tool/command, or a file containing one. You need this to understand the output format and design the parser.

### 3. Create the Parser

Create the parser file at `sensors/runners/parsers/<parser_name>.py`.

Implement all the methods defined in `base.py`, you'll find documentation for all of them in the file

**Keep concerns separated:** parsing, classification, aggregation, and result assembly are different jobs — don't fold them into one large `parse_output`. The same applies to failure formatting (parsing failures vs. rendering them per output style). Split helpers as needed so each function stays small and readable; see existing parsers for how far to take it.

**Key rules:**

- The runner strips ANSI escape codes before calling `parse_output`. Write regex for plain text only, never handle color codes.
- Handle parse errors gracefully: return `RunnerResult(success=False, output={"parseError": str(e), "raw": output[:500]})`.
- Use appropriate parsing strategy: JSON if available, regex for text, XML if structured.
- Terminal format methods use Rich markup: `[red]`, `[green]`, `[yellow]`, `[dim]`.
- Use these CSS class names: `sensors-error`, `sensors-warn`, `sensors-success`, `sensors-file`, `sensors-rule`, `sensors-message`, `sensors-violation`.
- LLM format methods use plain text only — no markup, no special characters.
- `format_failures_*` methods return empty string when `result.success is True`.

**Reference implementations** — read these for patterns:

- `sensors/runners/parsers/eslint.py` — JSON output parser with violations
- `sensors/runners/parsers/vitest.py` — text output parser with regex
- `sensors/runners/parsers/pytest.py` — text output parser with test results

### 4. Create Tests

Create the test file at `tests/test_<parser_name>.py`.

Implement at least one test for each of the parsing and formatting methods that you'll implement to satisfy the interface.

**Test conventions:**

- Use `pytest` with `pytest-asyncio` for async tests
- Use `@pytest.mark.asyncio` decorator for async test functions
- Instantiate: `parser = ParserClassName()`
- Call: `result = await parser.parse_output(output_string)`
- Assert that failure formatters return empty string when `result.success is True`
- Assert format methods return strings containing expected markup/classes/text

**Include an integration test** that runs the actual command, strips ANSI the same way the runner does, and parses the result. This catches mismatches between the regex and real tool output (e.g. ANSI codes, unexpected formatting). Pattern:

```python
@pytest.mark.asyncio
async def test_integration_full_pipeline():
    """Run the real command, strip ANSI, parse — same as the sensors runner."""
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    process = await asyncio.create_subprocess_shell(
        "uv run <tool command>",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=os.path.dirname(os.path.dirname(__file__)),
        env=env,
    )
    stdout, _ = await process.communicate()
    output = GenericRunner.strip_ansi(stdout.decode("utf-8", errors="ignore"))

    parser = ParserClassName()
    result = await parser.parse_output(output)

    # Key assertion: parsed detail count matches summary count
    # If these diverge, ANSI stripping or the regex is broken
    assert len(result.output["violations"]) == result.output["errorCount"]
    assert "no details" not in parser.format_failures_llm(result)
```

Import `GenericRunner` from `sensors.runners.generic` for the `strip_ansi` method. See `tests/test_ruff.py::test_integration_ruff_full_pipeline` for a complete example.

Run tests with: `uv run pytest tests/test_<parser_name>.py -v`

### 5. Register the Parser

Edit `sensors/runners/parsers/__init__.py`:

1. Add an import: `from .<parser_name> import <ParserClassName>`
2. Add registration: `ParserRegistry.register("<parser_name>", <ParserClassName>)`

Follow the existing pattern in the file.

### 6. Add Configuration

Add a runner entry to the sample config file (e.g., `config/default.sensors.yaml`):

```yaml
- name: runner-name
  parser: parser-name
  enabled: true
  mode: watch       # or "interval"
  command: command to run
  interval: 10000   # only for interval mode, in milliseconds
```

The `workingDir` field is optional. If omitted, the runner uses the project root (where the sensors CLI is started). If the runner needs to execute in a subdirectory, set `workingDir` to a relative path (e.g., `workingDir: ./ui`).

Tell the user in the summary that this runner entry was created, and that they should adjust it as needed.

**Mode selection:**

- `watch` — if the tool has native watch capability (vitest --watch, jest --watch, etc.)
- `interval` — for tools that need to be run periodically

If you don't know which one the user wants, ask them.

For interval mode, 5000–15000ms is typical.

### 7. Reinstall the CLI

After registering the new parser, reinstall the sensors CLI so the new parser is available globally:

```bash
uv tool install . --force --reinstall
```
The `--force` flag is needed to overwrite the existing installation.

### 8. Naming Conventions

- Parser name: lowercase, underscores for multi-word (e.g., `pytest_cov`)
- Class name: PascalCase + "Parser" suffix (e.g., `PytestCovParser`)
- File: `sensors/runners/parsers/<parser_name>.py`
- Test: `tests/test_<parser_name>.py`
- Config parser field matches the registered name exactly
