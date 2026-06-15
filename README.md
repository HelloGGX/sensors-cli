# Sensors Sidecar CLI

An **experimental** little "sidecar" system that can run a bunch of code quality sensors next to a coding agent. It can run linting, tests, and other checks on a schedule or in watch mode, persists structured state under `.sensors/` in the target codebase, and exposes a **`sensors`** CLI for running the service, checking the sensor status, or displaying the status in a human readable format.

Companion repository to this article: [Maintainability sensors for coding agents](https://martinfowler.com/articles/sensors-for-coding-agents.html)

Use `/_local-setup` skill to set it up on your machine (or use the `SKILL.md` file as documentation if you want to do it manually).

**Platform note:** The control plane uses **Unix domain sockets**, tested only on MacOS.

This tool was more or less vibe coded, though I did do regular refactorings with AI, and used the CLI to run sensors for this codebase ("eating my own dog food"). Check out [`.sensors/sensors-cli.sensors.yaml`](./.sensors/sensors-cli.sensors.yaml) to see the sensors used here. And look at [2026-06-15_modularity-review.md](./docs/reports/2026-06-15_modularity-review.md) for examples of why quick sensors like this can help with maintainability, but can only go so far when we don't spend much time on the larger code structure...

## Commands

(CLI needs to be installed via `uv tool install`, see `/_local-setup` skill)

```bash
# Is the sensors service running? (exit 0 = yes, 1 = no)
sensors status .

# All sensors start processes on this host (from /proc or ps)
sensors status --all

# Start the sensors
sensors start .

# Show the state
sensors show .

# Start the sensors and immediately jump into the display mode
sensors show --start .

# Agent-optimized runner results (failures included per runner); exit 0/1/2
sensors check .

sensors check . --runner eslint

# Save a score snapshot via RPC (needs a process to be running)
sensors snapshot .
```

## Configuration

The CLI looks for a `*.sensors.yaml` file under `.sensors/`. 

There are some skills in this repo that document this setup more and that you can reuse:
- `.claude/skills/sensors_config-default` - a minimalist default setup that tries to determine one sensor example from your codebase. Use this to just get a taste
- `.claude/skills/sensors_config-typescript` - my full Typescript sensors setup
- `.claude/skills/sensors_config-python` - my full Python sensors setup

## Parsers

The project comes with a bunch of output parsers for common tools, like `eslint` or `ruff`. If you want to use a tool as a sensor that is not yet supported, you either have to add a new parser to the code (and reinstall the CLI), or you can use the default parser.

### Adding a new parser

This repo contains a skill that documents how to add a new parser [`.claude/skills/_new-parser/SKILL.md`](/.claude/skills/_new-parser/SKILL.md) in this repo for a guided template.

## Default parser: Expected output format

Use `parser: default` in your runner config to connect any tool that can emit a JSON object in the specified schema. You have to build a script for your tool that turns the tool's output into this schema, and use that script in your sensor configuration.

This repo contains a skill that can help you write a wrapper script around your tool to transform your tool's data into the JSON schema [`.claude/skills/sensors_wrap-tool/SKILL.md`](/.claude/skills/sensors_wrap-tool/SKILL.md)

### Schema

```json
{
  "findings": [
    {
      "message": "Unused variable 'x'",
      "severity": "error",
      "file": "src/foo.py",
      "line": 42,
      "column": 9,
      "rule": "F841",
      "context": "x is assigned but never used"
    }
  ],
  "metrics": [
    {
      "key": "errorCount",
      "label": "Errors",
      "value": 1,
      "direction": "less"
    }
  ],
  "guidance": [
    {
      "rule": "F841",
      "body": "Remove variable or use it."
    }
  ],
  "score": {
    "value": 1,
    "direction": "less",
    "description": "Issues reported by tool"
  },
  "success": false,
  "summary": "1 issue",
  "extra": {
    "any": "parser-specific payload"
  }
}
```

This schema mirrors the `SensorReading` model used by built-in parsers. All fields are optional; missing values are derived as follows:

| Field | If absent or null |
|---|---|
| `findings` | treated as `[]` |
| `metrics` | treated as `[]` |
| `guidance` | treated as `[]` |
| `extra` | treated as `{}` |
| `success` | `true` when `findings` is empty, `false` otherwise |
| `summary` | `"N issue(s)"` / `"No issues"` derived from findings count |
| `score.value` | `len(findings)` |
| `score.direction` | `"less"` (lower is better) |
| `score.description` | `"Issues reported by tool"` |

`success`, `summary`, and `score` can be set explicitly and are used as-is. This allows tools that do not produce per-finding rows (for example, coverage checks) to report a score directly.


### Example config

```yaml
runners:
  - name: my-custom-check
    parser: default
    enabled: true
    mode: interval
    command: some-tool | ./scripts/to-parser-default-format.sh
    interval: 10000
```

### Minimal valid output

A tool that only reports a count without individual violations:

```json
{"success": false, "summary": "Coverage 72% (threshold 80%)", "score": {"value": 72, "direction": "more"}}
```

A tool with no issues:

```json
{"findings": []}
```
