# Sensors Sidecar Architecture

## How it fits together

```mermaid
flowchart TB
  subgraph cli [CLI same machine]
    startBg["sensors start ."]
    startShow["sensors start --show ."]
    attach["sensors show ."]
    status["sensors status ."]
    check["sensors check ."]
    snap["sensors snapshot ."]
    stop["sensors stop ."]
  end

  subgraph disk [Project .sensors per config]
    yaml["*.sensors.yaml"]
    stateJson["stem.state.json"]
    ctlJson["stem.control.json"]
    unixSock["stem.sock"]
  end

  subgraph process [OS process]
    orch[Orchestrator]
    runners[GenericRunners]
    display[DisplayManager optional]
    events[DisplayEvents]
    rpc[Unix RPC server]
  end

  subgraph external [Child processes]
    tools[eslint vitest ruff etc]
  end

  startBg -->|spawn worker| process
  startShow --> process
  attach -->|TUI reads| stateJson
  status -->|PID + socket| ctlJson
  check -->|read only| stateJson
  snap -->|JSON line RPC| unixSock
  stop -->|SIGTERM via PID in| ctlJson

  orch --> yaml
  orch --> runners
  orch --> display
  orch --> events
  rpc -->|snapshot ping| events
  runners --> tools
  runners -->|atomic writes| stateJson
  rpc --> ctlJson
  rpc --> unixSock
```

**Flows:**

- **`sensors start .`** — starts a **background** worker (no terminal UI). Writes `stem.control.json` (PID + socket path) and listens on **`stem.sock`** for RPCs (`ping`, `snapshot`).
- **`sensors start --show .`** — same process, but with the **live Rich table** (and keyboard: `S` snapshot, `C` clear/reload, `Q` quit).
- **`sensors show .`** — if a sensors is already running, opens an **attach** viewer in this terminal (reads the same `stem.state.json`; `Q` only exits the viewer).
- **`sensors status .`** — reports whether the sensors **process** is running (uses **`stem.control.json`**, live PID, and a **socket ping**). Exit `0` if running, `1` if not.
- **`sensors status --all`** — lists **every** **`start`** process on this machine (`--worker` or `--show`) by scanning **`/proc`** on Linux or **`ps`** elsewhere. The **project** path is the directory containing **`.sensors/`**, resolved in order: open **`.sensors/*.sock`** via **`lsof`** (same file the server binds), else **`.sensors/*.control.json`** (**`socketPath`** + **`pid`**), else process cwd / argv. On macOS, **`/private/var/...`** is shown as **`/var/...`** where equivalent.
- **`sensors check .`** — read **`stem.state.json`** and print per-runner results (no running process required for the read; you get exit `2` if nothing has written yet). Exit `0` / `1` / `2` like the old `check` command.
- **`sensors snapshot .`** — sends an RPC to the **running** process to save a **score snapshot** (same as pressing `S` in the TUI).
- **`sensors stop .`** — sends **SIGTERM** to the PID recorded in `stem.control.json` (graceful shutdown).

## Domain Model

```mermaid
classDiagram
    class SensorReading {
        +bool success
        +str summary
        +ScoreInfo score
        +list~Finding~ findings
        +list~Metric~ metrics
        +list~GuidanceBlock~ guidance
        +Formatted formatted
        +from_error(message) SensorReading
    }

    class ScoreInfo {
        +int value
        +str direction
        +str description
        +float threshold
    }

    class Formatted {
        +str summary_terminal
        +str summary_html
        +str summary_llm
        +str failures_terminal
        +str failures_html
        +str failures_llm
    }

    class Finding {
        +str message
        +str severity
        +str file
        +int line
        +str rule
    }

    class Metric {
        +str key
        +str label
        +float value
        +str unit
        +str direction
    }

    class GuidanceBlock {
        +str rule
        +str body
    }

    class RunnerEntry {
        +datetime lastRun
        +str status
        +str mode
        +SensorReading reading
    }

    class StateEntry {
        +datetime lastUpdated
        +dict~RunnerEntry~ runners
        +SnapshotEntry snapshot
        +list~QueryLogEntry~ queryLog
    }

    class SnapshotEntry {
        +str snapshot_id
        +datetime timestamp
        +dict~RunnerEntry~ runners
    }

    class HistoryEntry {
        +datetime timestamp
        +str runner_filter
        +str snapshot_id
        +dict~RunnerSummary~ runners
    }

    class RunnerSummary {
        +str status
        +ScoreInfo score
    }

    SensorReading --> ScoreInfo
    SensorReading --> Formatted
    SensorReading "1" --> "*" Finding
    SensorReading "1" --> "*" Metric
    SensorReading "1" --> "*" GuidanceBlock
    RunnerEntry --> SensorReading
    StateEntry "1" --> "*" RunnerEntry
    StateEntry --> SnapshotEntry
    SnapshotEntry "1" --> "*" RunnerEntry
    HistoryEntry "1" --> "*" RunnerSummary
    RunnerSummary --> ScoreInfo

```

## Module Layers

```mermaid
graph TD
    CLI["sensors.cli<br/><i>Typer entry point</i>"]

    subgraph Orchestration
        ORCH["sensors.orchestration.orchestrator<br/><i>Lifecycle, signals, config reload</i>"]
        CS["sensors.orchestration.control_server<br/><i>Unix socket RPC</i>"]
        PROC["sensors.orchestration.sensors_processes<br/><i>Process discovery (--all)</i>"]
    end

    subgraph Config
        CFG["sensors.config<br/><i>loader + schema (Pydantic)</i>"]
    end

    subgraph TUI
        DISP["sensors.tui.display<br/><i>DisplayManager (Rich)</i>"]
    end

    subgraph Runners
        GR["sensors.runners.generic<br/><i>GenericRunner</i>"]
        subgraph Parsers
            REG["sensors.runners.parsers<br/><i>ParserRegistry</i>"]
            BASE["parsers.base<br/><i>OutputParser ABC</i>"]
            IMPLS["pytest, ruff, eslint,<br/>depcruise, semgrep,<br/>vitest, ..."]
        end
    end

    subgraph Persistence
        SM["sensors.persistence.state_manager<br/><i>StateManager</i>"]
        MOD["sensors.persistence.models<br/><i>StateEntry, SnapshotEntry, ...</i>"]
    end

    CLI --> ORCH
    CLI --> CFG

    ORCH --> CFG
    ORCH --> GR
    ORCH --> REG
    ORCH --> SM
    ORCH --> CS

    GR --> REG
    GR --> SM

    REG --> BASE
    IMPLS --> BASE

    DISP --> SM
    SM --> MOD
```

