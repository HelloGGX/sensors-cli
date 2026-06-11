# Refactor Plan: Parsers Return Structured Data, Formatting Moves to a Generic Layer

## The Problem

`OutputParser` had 8 methods per parser:
`parse_output`, `calculate_score`, `format_details_terminal/html/llm`, `format_failures_terminal/html/llm`.

That is 8 x 14 parsers = **112 method implementations** to maintain. Most of the formatting
logic is mechanically identical across parsers (render a file:line:col, colour by severity,
list rule IDs) but was copied into each one. `RunnerResult.output` was typed `dict[str, Any]`,
so nothing checked that the keys a formatter reads actually exist.

`GenericRunner.on_result` called all 6 format methods in sequence, repeating the same
orchestration for every parser. The score was computed in a separate `calculate_score` call
even though it depends on the same parsed data.

The fix: parsers return **typed structured data**, and a single `GenericFormatter` handles
all rendering. Parsers drop to one method.

---

## Data Structures

`Finding`, `Metric`, `GuidanceBlock`, and `ParsedOutput` are defined in
`sensors/config/result_types.py` and exported from `sensors/config/__init__.py`.

- **`Finding`** — a single violation: `file`, `line`, `column`, `message`, `rule`, `severity`, `context`
- **`Metric`** — a named numeric value: `key`, `label`, `value`, `unit`, `direction`, `threshold`
- **`GuidanceBlock`** — per-rule guidance text: `rule`, `body`, `summary`
- **`ParsedOutput`** — what `parse()` returns: `success`, `summary`, `score`, `findings`, `metrics`, `guidance`, `extra`

`extra` is an escape hatch for data that doesn't fit the model (e.g. per-file coverage tables).
`GenericFormatter` ignores it.

### How this maps to every current parser

| Parser | `findings` | `metrics` | `guidance` | `extra` |
|---|---|---|---|---|
| `eslint` | file+line+col+rule+severity per message | errorCount, warningCount | triggered rules with guidance | — |
| `stylelint` | file+line+col+rule+severity per message | errorCount, warningCount | — | — |
| `ruff` | file+line+col+rule per diagnostic | errorCount | rule guidance blocks | — |
| `tsc` | file+line+col+code+message per error | errorCount, fileCount | — | — |
| `semgrep` | file+line+col+ruleId+severity per result | findingCount, errorCount | — | — |
| `depcruise` | file="src → dst", rule, severity, context=description | errorCount, warningCount | — | — |
| `import_linter` | file="source -> target", rule=contract, line, context=description | brokenCount, keptCount | — | — |
| `pytest` | rule=type+file+message per failure | passed, failed, errors, warnings | — | — |
| `vitest` | rule=type+test+message per failure | passed, failed | — | — |
| `pytest_cov` | — | totalCoverage%(threshold=80), passed, failed, misses | — | per-file table in `extra` |
| `vitest_cov` | — | totalBranch%(threshold=80), totalStmts%, totalFuncs% | — | per-file table in `extra` |
| `stryker` | — | mutationScoreOfCovered%(threshold=high from report), killed, survived, etc. | — | — |
| `git_diff` | — | linesAdded, linesRemoved, filesChanged, etc. | — | file list in `extra` |
| `default` | generic violations list | scoreValue | — | — |

---

## The New Parser Interface

`OutputParser` (`sensors/runners/parsers/base.py`) is now a plain base class (not an ABC).
The single method to implement is `parse(self, output: str) -> ParsedOutput`.

The default raises `NotImplementedError`, which `GenericRunner._parse_input` uses as the
signal to fall back to the legacy path. All other legacy methods (`parse_output`,
`calculate_score`, `format_*`) are concrete stubs that also raise `NotImplementedError`.

`parse` is synchronous — there is no I/O inside parsing, so `async` was never needed.
`calculate_score` disappears; the parser sets `ParsedOutput.score` directly.

---

## The Generic Formatter

`GenericFormatter` in `sensors/runners/formatter.py` renders a `ParsedOutput` into a
`FormattedOutput` for all three client types (terminal, HTML, LLM) via a single `format()`
call. Rendering logic for findings and guidance blocks lives in module-level helpers to
keep individual methods under the complexity threshold.

---

## Changes to `GenericRunner`

### `_parse_input` — the fallback bridge

`GenericRunner._parse_input(raw)` tries `parser.parse(raw)` first. If it raises
`NotImplementedError`, it falls back to `parser.parse_output(raw)`. It returns
`(ParsedOutput | None, RunnerResult)`. Both call sites (interval mode and watch mode)
use this method.

### `on_result` — dual path

`on_result(result, parsed=None)` routes on whether `parsed` is set:
- `parsed is not None` → `GenericFormatter.format(parsed)`, score from `parsed.score`
- `parsed is None` → legacy `format_*` calls and `calculate_score`

---

## When Generic Is Not Enough

Most parsers are fully covered by `Finding`, `Metric`, and `GuidanceBlock`. The only
remaining special case is:

### git_diff — always success, per-file table

The per-file breakdown does not fit `Finding` (no severity, no rule) and does not fit
`Metric` (it is a list of records). It goes in `extra["files"]` and `extra["testFiles"]`.
`git_diff` can override `_failures` in a `GenericFormatter` subclass. All other parsers
get generic rendering for free; `git_diff` opts in to a focused override.

---

## Migration Plan

### Step 1 — Add types and formatter (DONE)

- Added `ParsedOutput`, `Finding`, `Metric`, `GuidanceBlock` to `sensors/config/result_types.py`
  and exported from `sensors/config/__init__.py`.
- Added `GenericFormatter` to `sensors/runners/formatter.py`.
- Added default `parse()` raising `NotImplementedError` to `OutputParser`.
- All legacy abstract methods demoted to concrete stubs raising `NotImplementedError` —
  `OutputParser` is no longer an ABC.

### Step 2 — Wire `GenericRunner` (DONE)

Rather than waiting for all parsers to be migrated before touching `GenericRunner`, the
runner was wired up immediately with the `NotImplementedError` fallback:

- Added `_parse_input()`: tries `parse()`, catches `NotImplementedError`, falls back to
  `parse_output()`.
- Changed `on_result()` to accept `parsed: ParsedOutput | None = None` and route to
  `GenericFormatter` or legacy formatters accordingly.

Every parser that implements `parse()` is immediately live in production with no other
changes needed. Unmigrated parsers continue working exactly as before.

### Step 3 — Migrate parsers one at a time (IN PROGRESS)

For each parser, add `parse()` and delete all legacy methods and tests for those methods.

**Migrated (8/14):**
- `eslint` — `parse()` implemented; all legacy methods and tests deleted
- `stylelint` — `parse()` implemented; all legacy methods and tests deleted
- `vitest_cov` — `parse()` implemented; all legacy methods and tests deleted
- `depcruise` — `parse()` implemented; all legacy methods and tests deleted
- `semgrep` — `parse()` implemented; all legacy methods and tests deleted
- `stryker` — `parse()` implemented; all legacy methods and tests deleted
- `tsc` — `parse()` implemented; all legacy methods and tests deleted
- `default` — `parse()` implemented with `ParsedOutput`-shaped JSON schema; legacy methods and tests deleted

**Remaining (6/14):**
`ruff`, `import_linter`, `pytest`,
`pytest_cov`, `vitest`, `git_diff`

Migration checklist per parser:
1. Add `def parse(self, output: str) -> ParsedOutput` — implement using `Finding`,
   `Metric`, `GuidanceBlock` per the mapping table above.
2. Delete `parse_output`, `calculate_score`, and all `format_*` methods from the parser.
3. Delete corresponding legacy tests; keep or add `parse()`-based tests.
4. Run `sensors check .` to confirm no regressions.

### Step 4 — Remove legacy scaffolding (FUTURE)

Once all 14 parsers are migrated:
- Remove `parse_output`, `calculate_score`, and all `format_*` from `OutputParser`.
- Remove the `NotImplementedError` fallback branch from `_parse_input`.
- Remove the `parsed is None` branch from `on_result`.
- Update the module docstring in `base.py`.
- Change `RunnerResult.output` type annotation from `dict[str, Any]` to `ParsedOutput`
  (or remove `RunnerResult` entirely if no other code depends on it).

---

## Before/After Summary


## The Problem

`OutputParser` currently has 8 abstract methods per parser:

```
parse_output()
calculate_score()
format_details_terminal()
format_details_html()
format_details_llm()
format_failures_terminal()
format_failures_html()
format_failures_llm()
```

That is 8 x 14 parsers = **112 method implementations** to maintain. Most of the formatting
logic is mechanically identical across parsers (render a file:line:col, colour by severity,
list rule IDs) but is copied into each one. `RunnerResult.output` is typed `dict[str, Any]`,
so nothing checks that the keys a formatter reads actually exist.

`GenericRunner.on_result` then calls all 6 format methods in sequence, repeating the same
orchestration for every parser. The score is computed in a separate `calculate_score` call
even though it depends on the same parsed data.

The fix: parsers return **typed structured data**, and a single `GenericFormatter` handles
all rendering. Parsers drop to one method.

## Updating the skill

Once we're done with the refactoring, we also need to update our skill `_new-runner-type`. While we're at it, we should also rename it to `_new-parser`
