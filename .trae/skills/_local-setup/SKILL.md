---
name: _local-setup
description: Set up the live-dashboard project locally — install dashboard and sensor dependencies, and optionally install the sensors CLI as a global tool. Use when the user wants to get started, set up the project, or install dependencies.
---

# Local Setup

Set up the sensors-cli project on the local machine.

## Prerequisites

Before starting, verify the user has the required tools installed:

- **Python** >= 3.9
- **uv** — Python package manager

If any are missing, tell the user what to install before proceeding.

## Step-by-step Instructions

Before each of these steps, describe to the user what you are about to do, and ask them to confirm it, so that they understand what will happen and learn about this codebase while we are setting it up. So explain for each step why it is being done.

### Install CLI Dependencies

```bash
uv sync
```

### Install as a Global CLI Tool (Recommended)

This makes the `sensors` command available system-wide:

```bash
uv tool install .
```

If sensors was previously installed and you need to update it:

```bash
uv tool install . --force --reinstall
```

### Verify Installation

Run a quick check to confirm everything is working:

```bash
# Check sensors CLI is available
sensors --help

```

### Install pre-commit hook

Ask the user if they want to commit code in this repository, in which case they should set up the pre-commit hook.

```
pre-commit autoupdate
pre-commit install
```

### Summary

After setup, tell the user:

- To monitor a project, that project needs a `.sensors/*.sensors.yaml` config file — use the `/sensors_config-typescript` or `/sensors_config-python` skills to generate one, or as examples
- After config, start sensors in the target project with `sensors start --show .` (foreground UI) or `sensors start .` (background)
- To test this, run `sensors start --show .` in this very project, which has a sensors configuration