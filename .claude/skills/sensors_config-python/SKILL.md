---
name: sensors_config-python
description: Set up sensors (quality monitors) for a Python project. Detects which sensors are already configured, identifies missing ones from the supported list.
---

# Configure Sensors for a Python Project

This skill sets up sensor config files for a Python project using the supported sensor parsers. It is **Python only** — refuse if the project is not Python.

---

## Supported Sensors

### Maintainability (tool configs → `.sensors/maintainability/`)

| Sensor name | Parser | Underlying tool | What it monitors |
|-------------|--------|-----------------|-----------------|
| `tests` | `pytest` | pytest | Unit/integration test results |
| `cov` | `pytest_cov` | pytest --cov | Test coverage |
| `lint` | `ruff` | ruff check | Code style and quality issues |

### Security (tool configs → `.sensors/security/`)

| Sensor name | Parser | Underlying tool | What it monitors |
|-------------|--------|-----------------|-----------------|
| `security` | `ruff` | ruff check --select S | Bandit-equivalent security rules via ruff |

> Both `lint` and `security` use the `ruff` parser. They are intentionally split into two runners: `lint` runs all ruff rules **except** S-series (add `--extend-ignore S` or exclude via config), while `security` runs **only** S-series rules (`--select S`). This keeps maintainability and security concerns in separate sensor feeds.

---

## Tool Config Files

Each sensor's tool config file lives inside the `.sensors/<category>/` folder, not at the project root. This keeps quality tooling config co-located with the sensor definitions and away from the root.

**When a moveable tool config already exists at the root, move it** to the appropriate subfolder and update any references (Makefile targets, scripts, sensor commands). Ask the user to confirm the move.

| Tool | Root location (typical) | Move to |
|------|------------------------|---------|
| ruff (standalone) | `ruff.toml`, `.ruff.toml` | `.sensors/maintainability/ruff.toml` |
| pytest (standalone) | `pytest.ini` | `.sensors/maintainability/pytest.ini` |

**Do NOT move:**
- `pyproject.toml` — used by the build, editors, and all tools; must stay at root. If ruff or pytest config lives inside `pyproject.toml`, leave it there and point to it with `--config pyproject.toml`.
- `setup.cfg`, `setup.py` — fundamental project files; must stay at root.

Each tool that supports a `--config <path>` flag should use that flag pointing to the new location. The sensor `command` in the YAML must use the new path.

---

## Instructions

### Step 1 — Verify this is a Python project

Check for at least one of: `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, or `.py` files in the project. If none are found, **stop and tell the user** this skill is for Python projects only.

### Step 2 — Detect the package manager / runner

Determine how to invoke tools, as this affects every sensor command:

| Signal | Runner prefix |
|--------|--------------|
| `uv.lock` present | `uv run` |
| `poetry.lock` present | `poetry run` |
| `Pipfile.lock` present | `pipenv run` |
| `requirements.txt` only | `python -m` |
| None of the above | `python -m` (default) |

Use this prefix consistently in all sensor commands.

### Step 3 — Determine the project name

Derive from the directory name: lowercase, hyphens for spaces/underscores. Example: `my-app`.

### Step 4 — Check existing sensor configuration

Look for `.sensors/<project-name>.sensors.yaml`. Note which sensors are already configured in it — skip those in later steps. If it doesn't exist yet, all sensors are candidates.

### Step 5 — Analyse the project tooling

Read `pyproject.toml`, `setup.cfg`, and any other config files to identify what's installed and configured. For each sensor in the supported list that is **not yet configured**, determine:

- **Is the underlying tool installed?**
  - `pytest`: look for `pytest` in dev dependencies or `[tool.pytest.ini_options]` in pyproject.toml
  - `pytest_cov`: look for `pytest-cov` in dependencies
  - `ruff` (lint + security): look for `ruff` in dependencies or `[tool.ruff]` in pyproject.toml

- **Where does the tool config live?** Check for standalone config files at the root (see Tool Config Files above). Note whether they'll be moved or pointed to in-place.

- **What source path should be linted/tested?** Identify the main source directory (commonly the package directory, `src/`, or the project root).

### Step 6 — Research current CLI APIs before configuring

**Before generating the config for each sensor**, use web search to verify:
- Current recommended CLI flags for the tool
- The `--config <path>` flag syntax for pointing to a non-default config file location
- Any breaking changes in recent major versions

Pay special attention to:
- **pytest**: verify `-v` and `--tb=short` flags are current; verify `--config` flag if using a standalone `pytest.ini`.
- **pytest_cov**: verify `--cov=<package>`, `--cov-report=term-missing` flags and any `--cov-fail-under` option.
- **ruff** (lint): verify `--output-format full` flag and `--config` flag syntax.
- **ruff** (security): verify `--select S` selects the bandit rules, and confirm `--output-format full` still applies.

### Step 7 — Ask user to confirm each new sensor

For each sensor that is **not yet configured** and appears compatible, present a confirmation prompt that includes:

1. What the sensor monitors
2. Whether a tool config file already exists at the root and will be **moved** vs. pointed to in-place vs. created fresh
3. Any prerequisite (tool to install)

Do not configure a sensor the user did not confirm. It is fine to confirm multiple sensors at once with a clear checklist.

### Step 8 — Set up the directory structure

Create these directories if they don't exist:
- `.sensors/`
- `.sensors/maintainability/`
- `.sensors/security/`

Create `.sensors/.gitignore` if it doesn't exist, with:

```
# Sensors runtime state

events*.jsonl
sessions*.jsonl
*history.jsonl

*.log

*.state.json
*.control.json
*.sock
```

### Step 9 — Move or create tool config files

For each confirmed sensor:

1. **If a standalone tool config exists at the root** (e.g. `ruff.toml`, `pytest.ini`): move it to the appropriate `.sensors/<category>/` subfolder. Update any Makefile targets or scripts that reference it.
2. **If config lives in `pyproject.toml`**: leave it there; use `--config pyproject.toml` in the sensor command.
3. **If no config exists**: create a minimal working config in `.sensors/<category>/`. For example, a `ruff.toml` with sensible Python defaults and a `pytest.ini` with basic settings.

### Step 10 — Create or update the sensor YAML file

There is **one** sensors file: `.sensors/<project-name>.sensors.yaml`. It contains all runners regardless of category. The subfolders only hold tool config files.

If the file already exists, **append** only the newly confirmed sensors. Do not overwrite existing runners.

#### Config template

```yaml
version: 1
# prompt: Optional guidance for coding agents shown on every status query
runners:

  - name: <sensor-name>
    parser: <parser-name>
    enabled: true
    mode: interval
    command: <exact shell command>
    interval: <ms>
    workingDir: <relative path — omit if project root>
    # prompt: Optional per-sensor guidance for coding agents
```

#### Field rules

| Field | Rule |
|-------|------|
| `name` | Short, unique, descriptive. |
| `parser` | Must be one of the registered parsers in the table above. |
| `mode` | Always `interval`. |
| `command` | Exact shell command using the detected runner prefix. |
| `interval` | Set based on tool speed and how frequently the output changes (see guidance below). Spread intervals across runners so they don't all fire simultaneously. |
| `workingDir` | Omit to use project root. Set if runner must run in a subdirectory. |

#### Interval guidance

Choose intervals based on two factors: **how fast the tool runs** and **how often its output meaningfully changes**. Spread values across runners so they don't all trigger at the same moment.

| Sensor | Suggested interval | Rationale |
|--------|-------------------|-----------|
| `lint` (ruff) | 10 000 ms | Very fast; catches issues as you edit |
| `tests` (pytest) | 15 000 ms | Moderate speed; run frequently but not on every keystroke |
| `security` (ruff --select S) | 30 000 ms | Same tool speed, but security posture changes less often than style |
| `cov` (pytest --cov) | 60 000 ms | Slow; coverage only shifts when tests or source change meaningfully |

Adjust up if the project's test suite is large/slow. The goal is that no two sensors fire at exactly the same second during normal coding.

#### Parser-specific command notes

- **pytest** (tests): `uv run pytest tests/ -v --tb=short`
- **pytest_cov** (coverage): `uv run pytest tests/ --cov=<package> --cov-report=term-missing`
- **ruff** (lint): Run all rules except S-series to avoid overlap with the security runner.
  - Preferred: exclude S via config (`extend-ignore = ["S"]` in `ruff.toml` or `pyproject.toml`)
  - Or pass inline: `uv run ruff check --extend-ignore S --output-format full --config .sensors/maintainability/ruff.toml src/`
  - The `workingDir` field can be used instead of specifying the path in the command (see example below)
- **ruff** (security): Select only S-series rules. Specify the source path directly in the command.
  - `uv run ruff check --select S --output-format full src/`
  - Give this runner the name `security` and add a `prompt` explaining these are bandit-equivalent security rules.

Replace `uv run` with the detected runner prefix from Step 2.

---

## Step 11 — Present the summary

After creating/updating the files, tell the user:
- Which sensors were added to `.sensors/<project-name>.sensors.yaml`
- Which tool config files were moved and from where
- Which sensors were skipped and why (already configured, incompatible, not confirmed)
- Any prerequisites still needed (tool installs)
- That `prompt` fields can be added to sensor runners to give coding agents context-specific guidance

---

## Example output structure

```
.sensors/
  .gitignore
  my-app.sensors.yaml        ← single file, all runners
  maintainability/
    ruff.toml                ← moved from project root (or created fresh)
    pytest.ini               ← moved from project root (or created fresh)
  security/
                             ← no files needed if ruff config is in pyproject.toml
```

### Example sensors YAML

```yaml
version: 1
runners:

  - name: lint
    parser: ruff
    enabled: true
    mode: interval
    command: uv run ruff check --output-format full
    workingDir: src/
    interval: 10000
    # ruff.toml or pyproject.toml should extend-ignore S to avoid overlap with the security runner

  - name: tests
    parser: pytest
    enabled: true
    mode: interval
    command: uv run pytest tests/ -v --tb=short
    interval: 15000

  - name: security
    parser: ruff
    enabled: true
    mode: interval
    command: uv run ruff check --select S --output-format full src/
    workingDir: src/
    interval: 30000
    prompt: "These are bandit-equivalent security rules (ruff S-series). Review each finding before dismissing — false positives exist but should be consciously evaluated."

  - name: cov
    parser: pytest_cov
    enabled: true
    mode: interval
    command: uv run pytest tests/ --cov=my_app --cov-report=term-missing
    interval: 60000
```
