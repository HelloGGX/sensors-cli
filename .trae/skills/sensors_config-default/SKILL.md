---
name: sensors_config-default
description: Set up basic sensors CLI configuration for a codebase. Asks user to identify an example tool or test in the codebase and configures it with the default parser.
---

# Basic Sensors Configuration Setup

This skill sets up a minimal sensors configuration for any project. It creates a `.sensors/` directory structure with basic YAML and `.gitignore` files, then helps set up a starter example runner using the `default` parser.

---

## Default Parser

For a minimal setup, use the **default parser**, which accepts any tool that can emit JSON in a standardized sensors schema.

**For the complete schema documentation, field explanations, and examples**, see the `sensors_wrap-tool` skill — it documents the JSON format in full detail and includes guidance on building wrapper scripts to adapt tools that don't emit sensors-compatible JSON.

For language-specific sensors (pytest, ruff, eslint, vitest, etc.), refer to the language-specific skills: `sensors_config-python` or `sensors_config-typescript`.

---

## Instructions

### Step 1 — Verify the project exists

Confirm that there is a project directory with at least some source code or configuration files. If not, guide the user to create one first.

### Step 2 — Determine the project name

Derive the project name from the directory name, using lowercase and hyphens for spaces/underscores. Example: `my-project`.

### Step 3 — Check for existing sensor configuration

Look for `.sensors/<project-name>.sensors.yaml` in the project root. If it exists, tell the user and ask whether to:
- **Append** new runners to it
- **Replace** it
- **Skip** setup

If it doesn't exist, proceed to Step 4.

### Step 4 — Create the `.sensors/` directory structure

Create the following directories:
```
.sensors/
└── (tool configs and wrapper scripts will go here)
```

### Step 5 — Create `.sensors/.gitignore`

Create a `.gitignore` file in the `.sensors/` directory with the following content:

```
# Sensors runtime state

*history.jsonl
*.log
*.state.json
*.control.json
*.sock
```

This prevents sensor state files from being committed to version control.

### Step 6 — Ask user to pick a first sensor

Present a set of questions to the user:

1. **Do you have a tool or test runner you'd like to use as a sensor?**
   - Provide examples: "e.g., a test runner (pytest, vitest), a linter, a coverage tool, a mutation tester, or any custom tool that can emit JSON"

2. **Find the setup of the tool the user provides in the codebase, and the command to start it**

3. **Assume a wrapper script is needed (this is expected).**
   - Output of that tool will not match the sensors default-parser schema directly.
   - Refer to the `sensors_wrap-tool` skill to build the wrapper script and wire it into the runner command.
   

### Step 7 — Suggest a minimal sensor if user is unsure

If the user is unsure what tool to set up, suggest a minimal example with placeholders:

```yaml
  - name: my-custom-tool
    parser: default
    enabled: true
    mode: interval
    command: ./scripts/my-tool.sh
    interval: 30000
```

Explain that they can use this template and fill in:
- `name`: Short descriptive name
- `command`: The actual command to run
- `interval`: How often to run (in milliseconds; 30000 = 30 seconds is a safe default)

### Step 8 — Create the initial `.sensors.yaml`

Generate the sensors YAML file at `.sensors/<project-name>.sensors.yaml` with:

1. **Version header**:
   ```yaml
   version: 1
   runners:
   ```

2. **The example runner** based on user input:
   ```yaml
     - name: <tool-name>
       parser: default
       enabled: true
       mode: interval
       command: <user's command>
       interval: 30000
   ```

If the tool output requires wrapping, add a note in a comment:
```yaml
# NOTE: This tool's output needs to be wrapped to match the sensors schema.
# Agent action: use .claude/skills/sensors_wrap-tool/SKILL.md to build and wire the adapter script.
```

### Step 9 — Summary and next steps

After creating the config, tell the user:

1. **Config location**: `.sensors/<project-name>.sensors.yaml`
2. **Next steps**:
   - The agent should build and wire the wrapper script via `sensors_wrap-tool`
   - Then test with `sensors start --show .` to confirm parsing works
   - To add more sensors: use `sensors_config-python` or `sensors_config-typescript` for language-specific tools
3. **To start monitoring**: `sensors start --show .` (foreground with UI) or `sensors start .` (background)
4. **To check status**: `sensors check .`


