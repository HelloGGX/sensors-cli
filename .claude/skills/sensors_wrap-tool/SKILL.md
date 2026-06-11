---
name: sensors_wrap-tool
description: Build a wrapper script that adapts any tool's output to the sensors CLI's default parser JSON schema, then wire it into a sensor config.
---

# Wrap a Tool for the Sensors Default Parser

Use this skill when the user wants to connect a tool to sensors that does not have a dedicated parser. The result is a script (wrapper or pipe filter) that emits JSON the `default` parser understands, plus a sensor config entry.

---

## What the default parser expects

The parser looks for the **first JSON object** in stdout. Text before and after it is ignored, so print-statements and banner lines in the same output are fine.

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
      "context": "variable is assigned but never used"
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
      "body": "Remove the variable or use it in your code."
    }
  ],
  "score": {
    "value": 1,
    "direction": "less",
    "description": "Issues reported by tool"
  },
  "success": false,
  "summary": "1 issue",
  "extra": {}
}
```

All fields are optional. Missing ones are derived:

| Field | Derived as |
|---|---|
| `findings` | `[]` |
| `metrics` | `[]` |
| `guidance` | `[]` |
| `extra` | `{}` |
| `success` | `true` when findings is empty |
| `label` | `"N issue(s)"` / `"No issues"` |
| `score.value` | `len(findings)` |
| `score.direction` | `"less"` (lower is better) |
| `score.description` | `"Issues reported by tool"` |

Each finding object: only `message` is required. Other fields (`severity`, `file`, `line`, `column`, `rule`, `context`) are optional and used for formatting and grouping. `severity` defaults to `"error"`; use `"warning"` or `"info"` for lower-severity items.

Use `success`, `label`, and `score` directly when the tool produces a single metric rather than a list of findings -- e.g. a coverage percentage:

```json
{"success": false, "summary": "Coverage 72% (threshold 80%)", "score": {"value": 72, "direction": "more"}}
```

The `metrics`, `guidance`, and `extra` sections are optional and primarily useful when:
- **metrics:** you want to track specific measurements instead of or alongside findings (e.g., coverage percentage, mutation score, error count)
- **guidance:** you want to provide rule-specific advice tied to findings
- **extra:** you need to store parser-specific data that doesn't fit the standard model (e.g., per-file coverage tables)

---

## Step-by-step instructions

### Step 1 -- Identify the tool and its output

Ask the user:
1. What command do they want to run as a sensor?
2. Can they share a sample of the tool's output? (run it, paste the result, or point to a fixture file)

If no sample is available, offer to run the command yourself (only if it is safe and read-only). You need real output before writing the parser.

### Step 2 -- Identify what to extract

Look at the sample output and determine:

- **Is there a structured format** (JSON, XML, CSV) or plain text?
- **What signals pass/fail?** (exit code, a word in the output, a count)
- **What are the individual findings?** (file, line, message, rule ID, severity, context)
- **Are there metrics** (counts, percentages, thresholds)?
- **Is there guidance text** tied to specific rules?
- **Is there a single score** instead of a list of findings?
- **Any parser-specific data** that doesn't fit findings/metrics/guidance (store in `extra`)?

Describe your reading to the user before writing any code, and confirm it is correct.

### Step 3 -- Choose a script language

| Situation | Preferred language |
|---|---|
| Project has Python (pyproject.toml, .py files, uv/poetry) | Python |
| Project is JS/TS only, no Python available | Node.js (plain JS, no deps) |
| Simple line-count or grep transform | POSIX shell |

Use the simplest language that handles the parsing reliably. Avoid adding new runtime dependencies -- stick to the stdlib of the chosen language.

### Step 4 -- Choose an integration pattern

**Pattern A -- Wrapper:** the script calls the tool itself, captures its output, transforms it to JSON, and prints it.

```
command: python ./.sensors/scripts/my-tool-sensor.py
```

Use this when you need to capture both stdout and stderr, or when the tool exits non-zero on findings (which would otherwise confuse the runner).

**Pattern B -- Pipe filter:** the script reads from stdin and emits JSON.

```
command: my-tool --flags | python ./.sensors/scripts/my-tool-sensor.py
```

Use this when the tool's stdout is the only input and exit codes are not an issue.

When in doubt, prefer **Pattern A** -- it gives the script full control over exit codes and output capture.

### Step 5 -- Write the script

Place the script in `.sensors/scripts/` (create the directory if needed), and implement the parsing and creation of the JSON output.

### Step 7 -- Make the script executable and test it

```bash
chmod +x .sensors/scripts/<script-name>
```

Run it manually and confirm the JSON is valid:

```bash
# Pattern A
python ./.sensors/scripts/<script-name>.py | python -m json.tool

# Pattern B
<tool> <args> | python ./.sensors/scripts/<script-name>.py | python -m json.tool
```

Fix any issues before wiring it into the sensor config.

### Step 8 -- Add the sensor config entry

Add a runner to `.sensors/<project-name>.sensors.yaml`. If the file does not exist yet, check the project's `.sensors/` directory for any `*.sensors.yaml` file and use that.

```yaml
  - name: <sensor-name>
    parser: default
    enabled: true
    mode: interval
    command: <full command including interpreter>
    interval: <ms>
    # prompt: Optional guidance for coding agents shown in sensor output
```

Interval guidance:

| Tool speed | Suggested interval |
|---|---|
| Fast (< 2s) | 10 000 ms |
| Medium (2-10s) | 15 000 -- 30 000 ms |
| Slow (> 10s) | 60 000 ms |

We want the intervals in the config file to be unevenly spread, so they don't frequently run at the same time.
