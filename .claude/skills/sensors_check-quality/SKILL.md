---
name: sensors_check-quality
description: Query the sensors monitoring system for test, lint, coverage and overall status. Use when the user asks to check sensors status or quality, get test/lint failures, verify code quality. Use this instead of directly calling test and analysis commands via bash. Sensors should always be checked as part of the "definition of done" when wrapping up a task.
---

# Query Sensor Status

Sensors is a continuous code quality monitoring system. It runs tests, linting, and coverage checks in the background.

You should always query sensors over running test and lint commands yourself, as sensors is a more efficient way to provide you with the data! ONLY if the sensors results are not enough for you to analyse what the problem is should you run the commands directly.

The runners are configured in `default.sensors.yaml` inside the target project's `.sensors/` directory. That can give you an idea of what data is available.

## Query CLI

`sensors` CLI should be installed globally. All commands take the **project working directory** (the directory containing `.sensors/`) as the first argument. When querying the project you're currently working in, pass `.` as the path.

### Is sensors running (service-style)

```bash
sensors status .
```

Exit codes: `0` = sensors process is running, `1` = not running.

### Full status of all runners (includes failure details)

```bash
sensors check .
```

Shows status of all runners with failure details inline. This is the primary command to use for quality/readiness.

### Filter to a specific runner

```bash
sensors check . --runner tests
sensors check . --runner eslint
```

Exit codes: `0`=all pass, `1`=failures exist, `2`=error/no state.

## Reading State Directly

State files live in the project's `.sensors/` directory as `*.state.json`. Key fields per runner:

- `status`: `"success"` or `"failure"`
- `formatted.details_llm`: one-line summary (e.g., "1 failed, 26 passed")
- `formatted.failures_llm`: full failure details in plain text
- `score.value`: numeric score (lower is better for errors, higher is better for coverage)
- `score.direction`: `"less"` (fewer is better) or `"more"` (higher is better)

## IMPORTANT: Do Not Suppress Errors Without User Approval

When fixing sensor failures, NEVER silently suppress errors by adding ignore directives (e.g. `eslint-disable`, `@ts-ignore`, `// nolint`, `.eslintignore` entries, etc.) or by concluding that a failure is "not relevant for our changes" without explicitly confirming with the user first. Always ask the user whether suppressing or ignoring a specific error is warranted before doing so.

## Typical Workflow

1. Run `sensors check .` to see current state and what's broken
2. Fix the reported issues
3. Wait a few seconds for sensors to re-run (watch mode is near-instant, interval mode up to 15s)
4. Run `sensors check .` again to confirm fixes
