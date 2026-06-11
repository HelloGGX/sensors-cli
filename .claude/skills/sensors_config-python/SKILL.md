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
| `ruff` (or `lint`) | `ruff` | ruff check | Code style and quality issues |
| `imports` | `import_linter` | import-linter (`lint-imports`) | Import/dependency contracts (optional) |

### Security (tool configs → `.sensors/security/` or second ruff runner)

| Sensor name | Parser | Underlying tool | What it monitors |
|-------------|--------|-----------------|-----------------|
| `ruff-sec` (or `security`) | `ruff` | ruff check --select S | Bandit-equivalent security rules via ruff |

> Both maintainability and security ruff runners use the `ruff` parser. Split them into two sensors: one runs the project's normal rule set from `pyproject.toml` (do **not** include `S` in `[tool.ruff.lint] select`), the other runs **only** S-series (`--select S`). This keeps maintainability and security in separate feeds.

---

## Ruff custom guidance

This skill includes a Ruff wrapper and overrides (in `maintainability/` relative to this skill file). It runs `ruff check` with JSON output and appends per-rule guidance for coding agents — same idea as the TypeScript skill's ESLint formatters.

| File | Purpose |
|------|---------|
| `ruff_guidance.py` | Wrapper: invokes ruff, merges diagnostics with guidance from overrides |
| `ruff_guidance_overrides.py` | `RULE_GUIDANCE` dict — short text + long guidance per rule code |

Both files are **copied** (not moved) from the skill's `maintainability/` folder into `.sensors/maintainability/` in the target project. They have **no dependencies** beyond the stdlib; ruff itself is resolved via PATH, `.venv`, `RUFF=`, or `uv run ruff`.

**After copying**, adapt `ruff_guidance_overrides.py` for rules the project enables that benefit from agent context (complexity, argument count, long functions, etc.). Keys must match Ruff's `code` field (e.g. `C901`, `PLR0913`).

### Sensor commands

Use `python` for the wrapper (it finds ruff itself). Pass ruff arguments after `--`:

```yaml
  - name: ruff
    parser: ruff
    enabled: true
    mode: interval
    command: python ./.sensors/maintainability/ruff_guidance.py -- check src/
    interval: 10000

  - name: ruff-sec
    parser: ruff
    enabled: true
    mode: interval
    command: python ./.sensors/maintainability/ruff_guidance.py -- --select S check src/
    interval: 30000
    prompt: "Bandit-equivalent security rules (ruff S-series). Review each finding before dismissing."
```

Replace `src/` with the project's source path. Use `--` before ruff flags (e.g. `--select S`).

### Detecting an existing ruff wrapper

Before copying, check whether the project already wraps ruff for sensors:

- Look for `ruff_guidance.py` under `.sensors/maintainability/`
- Look for sensor commands that invoke a custom script instead of bare `ruff check`

If a wrapper **already exists**: ask whether to replace it, keep it, or merge `RULE_GUIDANCE` entries into the existing overrides file.

If **none** exists: copy both files and use the commands above.

---

## Ruff config in `pyproject.toml`

**Leave ruff configuration in root `pyproject.toml`** — do not move it to `.sensors/`. The wrapper and `ruff check` both pick up `[tool.ruff]` from the project root.

When adding or extending ruff for sensors, prefer a focused rule set in `[tool.ruff.lint]` with documented thresholds. Example (adapt paths and thresholds to the project):

```toml
[tool.ruff]
line-length = 100
target-version = "py310"

[tool.ruff.lint]
select = [
    "E",        # pycodestyle errors
    "F",        # Pyflakes
    "I",        # isort
    "W",        # pycodestyle warnings
    "UP",       # pyupgrade
    "B",        # flake8-bugbear
    "SIM",      # flake8-simplify
    "C901",     # McCabe complexity
    "PLR0913",  # too many arguments
    "PLR0915",  # too many statements
    "PLR0917",  # too many positional-only parameters
]
ignore = [
    "E501",  # line length (formatter handles this)
    "W291",  # trailing whitespace
]
# Do not add "S" here — use the separate ruff-sec runner

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.pylint]
max-args = 5
max-statements = 50
```

Add matching entries to `ruff_guidance_overrides.py` for rules where agents need refactor-vs-noqa context (`C901`, `PLR0913`, `PLR0915`, etc.).

Ensure `ruff` is in dev dependencies (e.g. `uv add --dev ruff`).

---

## Tool Config Files

Each sensor's tool config file lives inside the `.sensors/<category>/` folder, not at the project root. This keeps quality tooling config co-located with the sensor definitions and away from the root.

**When a moveable tool config already exists at the root, move it** to the appropriate subfolder and update any references (Makefile targets, scripts, sensor commands). Ask the user to confirm the move.

| Tool | Root location (typical) | Move to |
|------|------------------------|---------|
| ruff (standalone only) | `ruff.toml`, `.ruff.toml` | `.sensors/maintainability/ruff.toml` — **prefer keeping rules in `pyproject.toml`** |
| pytest (standalone) | `pytest.ini` | `.sensors/maintainability/pytest.ini` |
| import-linter | `.importlinter` | `.sensors/maintainability/.importlinter` (if not using pyproject) |

**Do NOT move:**
- `pyproject.toml` — used by the build, editors, and all tools; must stay at root. Ruff, pytest, and import-linter config usually lives here.
- `setup.cfg`, `setup.py` — fundamental project files; must stay at root.

**Copy into `.sensors/maintainability/` (from this skill):**
- `ruff_guidance.py`, `ruff_guidance_overrides.py` — always copy when setting up ruff sensors (see above).

Each tool that supports a `--config <path>` flag should use that flag pointing to the new location when config was moved. Ruff in `pyproject.toml` needs no `--config` flag.

---

## Instructions

### Step 1 — Verify this is a Python project

Check for at least one of: `pyproject.toml`, `setup.py`, `setup.cfg`, `requirements.txt`, or `.py` files in the project. If none are found, **stop and tell the user** this skill is for Python projects only.

### Step 2 — Detect the package manager / runner

Determine how to invoke tools, as this affects sensor commands (except the ruff wrapper, which auto-detects ruff):

| Signal | Runner prefix |
|--------|--------------|
| `uv.lock` present | `uv run` |
| `poetry.lock` present | `poetry run` |
| `Pipfile.lock` present | `pipenv run` |
| `requirements.txt` only | `python -m` |
| None of the above | `python -m` (default) |

Use this prefix for pytest, coverage, and import-linter commands. Ruff sensors use `python ./.sensors/maintainability/ruff_guidance.py`.

### Step 3 — Determine the project name

Derive from the directory name: lowercase, hyphens for spaces/underscores. Example: `my-app`.

### Step 4 — Check existing sensor configuration

Look for `.sensors/<project-name>.sensors.yaml`. Note which sensors are already configured in it — skip those in later steps. If it doesn't exist yet, all sensors are candidates.

### Step 5 — Analyse the project tooling

Read `pyproject.toml`, `setup.cfg`, and any other config files to identify what's installed and configured. For each sensor in the supported list that is **not yet configured**, determine:

- **Is the underlying tool installed?**
  - `pytest`: look for `pytest` in dev dependencies or `[tool.pytest.ini_options]` in pyproject.toml
  - `pytest_cov`: look for `pytest-cov` in dependencies
  - `ruff` (maintainability + security): look for `ruff` in dependencies or `[tool.ruff]` in pyproject.toml
  - `import_linter`: look for `import-linter` and `[tool.importlinter]` or `.importlinter`

- **Where does the tool config live?** Check for standalone config files at the root (see Tool Config Files above). Ruff rules should usually stay in `pyproject.toml`.

- **What source path should be linted/tested?** Identify the main source directory (commonly the package directory, `src/`, or the project root).

- **Is `ruff_guidance.py` already present?** If not, plan to copy it from this skill.

### Step 6 — Research current CLI APIs before configuring

**Before generating the config for each sensor**, use web search to verify:
- Current recommended CLI flags for the tool
- The `--config <path>` flag syntax for pointing to a non-default config file location
- Any breaking changes in recent major versions

Pay special attention to:
- **pytest**: verify `-v` and `--tb=short` flags are current; verify `--config` flag if using a standalone `pytest.ini`.
- **pytest_cov**: verify `--cov=<package>`, `--cov-report=term-missing` flags and any `--cov-fail-under` option.
- **ruff**: confirm `check` subcommand and JSON output; security runner uses `--select S`. Sensors use `ruff_guidance.py` instead of calling `ruff` directly with `--output-format`.
- **import-linter**: verify `lint-imports` entry point and contract file location.

### Step 7 — Ask user to confirm each new sensor

For each sensor that is **not yet configured** and appears compatible, present a confirmation prompt that includes:

1. What the sensor monitors
2. Whether a tool config file already exists at the root and will be **moved** vs. pointed to in-place vs. created fresh
3. Whether ruff guidance scripts will be **copied** from this skill
4. Any prerequisite (tool to install)

Do not configure a sensor the user did not confirm. It is fine to confirm multiple sensors at once with a clear checklist.

### Step 8 — Set up the directory structure

Create these directories if they don't exist:
- `.sensors/`
- `.sensors/maintainability/`
- `.sensors/security/` (optional if security ruff runner is used without extra files)

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

### Step 9 — Move, copy, or create tool config files

For each confirmed sensor:

1. **Ruff guidance**: copy `maintainability/ruff_guidance.py` and `maintainability/ruff_guidance_overrides.py` from this skill into `.sensors/maintainability/`. Customize overrides for enabled rules.
2. **Ruff rules**: add or extend `[tool.ruff]` in `pyproject.toml` (see Ruff config section). Omit `S` from `select`; use a separate `ruff-sec` runner.
3. **If a standalone tool config exists at the root** (e.g. `pytest.ini`): move it to `.sensors/<category>/` and update references.
4. **If no config exists**: create minimal config in `pyproject.toml` or `.sensors/<category>/` as appropriate.

### Step 10 — Create or update the sensor YAML file

There is **one** sensors file: `.sensors/<project-name>.sensors.yaml`. It contains all runners regardless of category. The subfolders hold tool configs and wrapper scripts.

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
| `mode` | Always `interval` (unless documented otherwise for a specific parser). |
| `command` | Exact shell command. |
| `interval` | Set based on tool speed and how frequently the output changes (see guidance below). Spread intervals across runners so they don't all fire simultaneously. |
| `workingDir` | Omit to use project root. Set if runner must run in a subdirectory. |

#### Interval guidance

| Sensor | Suggested interval | Rationale |
|--------|-------------------|-----------|
| `ruff` | 10 000 ms | Fast; catches issues as you edit |
| `tests` (pytest) | 15 000 ms | Moderate speed |
| `ruff-sec` | 30 000 ms | Same tool; security posture changes less often |
| `imports` (import-linter) | 25 000–30 000 ms | Structural; changes less often than style |
| `cov` (pytest --cov) | 60 000 ms | Slow; coverage shifts when tests or source change |

#### Parser-specific command notes

- **pytest** (tests): `uv run pytest tests/ -v --tb=short`
- **pytest_cov** (coverage): `uv run pytest tests/ --cov=<package> --cov-report=term-missing`
- **ruff** (maintainability): `python ./.sensors/maintainability/ruff_guidance.py -- check <src>/`
- **ruff** (security): `python ./.sensors/maintainability/ruff_guidance.py -- --select S check <src>/`
- **import_linter**: `uv run lint-imports` (requires contracts in pyproject or `.importlinter`)

Replace `uv run` with the detected runner prefix from Step 2 where applicable (not for `ruff_guidance.py`).

---

## Step 11 — Present the label

After creating/updating the files, tell the user:
- Which sensors were added to `.sensors/<project-name>.sensors.yaml`
- That `ruff_guidance.py` and `ruff_guidance_overrides.py` were copied and which rule overrides to customize
- Which `[tool.ruff]` sections were added or updated in `pyproject.toml`
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
    ruff_guidance.py         ← copied from this skill
    ruff_guidance_overrides.py
    pytest.ini               ← only if moved from project root
```

Ruff rule selection and thresholds live in root `pyproject.toml` under `[tool.ruff]`.

### Example sensors YAML

```yaml
version: 1
runners:

  - name: tests
    parser: pytest
    enabled: true
    mode: interval
    command: uv run pytest tests/ -v
    interval: 15000

  - name: cov
    parser: pytest_cov
    enabled: true
    mode: interval
    command: uv run pytest tests/ --cov=my_app --cov-report=term-missing
    interval: 60000

  - name: ruff
    parser: ruff
    enabled: true
    mode: interval
    command: python ./.sensors/maintainability/ruff_guidance.py -- check my_app/
    interval: 10000

  - name: ruff-sec
    parser: ruff
    enabled: true
    mode: interval
    command: python ./.sensors/maintainability/ruff_guidance.py -- --select S check my_app/
    interval: 30000
    prompt: "Bandit-equivalent security rules (ruff S-series). Review each finding before dismissing."

  - name: imports
    parser: import_linter
    enabled: true
    mode: interval
    command: uv run lint-imports
    interval: 26000
```
