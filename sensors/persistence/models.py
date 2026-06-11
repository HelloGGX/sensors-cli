"""State models for persistence layer."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from sensors.config.result_types import Finding, ScoreInfo, SensorReading


class RunnerEntry(BaseModel):
    """State for a single runner."""

    lastRun: datetime = Field(
        description="When this runner last executed"
    )
    status: Literal["success", "failure", "below_threshold"] = Field(
        description="Current status of the runner"
    )
    mode: str = Field(
        default="",
        description="Human-readable mode description (e.g. 'watch', 'every 5s')"
    )
    reading: SensorReading | None = Field(
        default=None,
        description="Structured sensor reading with pre-computed formatted output"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class QueryLogEntry(BaseModel):
    """A single query log entry."""

    timestamp: datetime = Field(
        description="When the query was made"
    )
    command: str = Field(
        description="The command that was executed (check, failures, …)"
    )
    runner: str | None = Field(
        default=None,
        description="Runner filter if specified"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SnapshotEntry(BaseModel):
    """A point-in-time snapshot of all runner states for comparison."""

    snapshot_id: str = Field(
        description="Unique identifier for this snapshot, used to group history records"
    )
    timestamp: datetime = Field(
        description="When the snapshot was taken"
    )
    runners: dict[str, RunnerEntry] = Field(
        default_factory=dict,
        description="Runner states at snapshot time"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class RunnerSummary(BaseModel):
    """Compact status/score summary for a single runner."""

    status: Literal["success", "failure", "below_threshold"] = Field(
        description="Runner status at check time"
    )

    score: ScoreInfo | None = Field(
        default=None,
        description="Domain score for summary and trend analysis"
    )


class HistoryRunnerEntry(BaseModel):
    """Detailed per-runner payload stored in history.jsonl."""

    status: Literal["success", "failure", "below_threshold"] = Field(
        description="Runner status at check time"
    )
    score: dict[Literal["value", "direction"], int | Literal["more", "less"]] | None = Field(
        default=None,
        description="Compact score at check time"
    )

    findings: list[Finding] = Field(
        default_factory=list,
        description="Structured findings at check time, if available"
    )


class HistoryEntry(BaseModel):
    """One record written to history.jsonl on every 'check' command."""

    timestamp: datetime = Field(
        description="When the check was executed"
    )
    runner_filter: str | None = Field(
        default=None,
        description="--runner filter that was passed, or None if all runners were checked"
    )
    snapshot_id: str | None = Field(
        default=None,
        description="ID of the active snapshot when this check was run; groups checks taken between two snapshots",
    )
    runners: dict[str, HistoryRunnerEntry] = Field(
        default_factory=dict,
        description="Per-runner status, score, and findings at check time"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class StateEntry(BaseModel):
    """Overall sensors state containing all runners."""

    lastUpdated: datetime = Field(
        description="When the sensors state was last updated"
    )
    runners: dict[str, RunnerEntry] = Field(
        default_factory=dict,
        description="State for each runner, keyed by runner name"
    )
    snapshot: SnapshotEntry | None = Field(
        default=None,
        description="Point-in-time snapshot for before/after comparison"
    )
    queryLog: list[QueryLogEntry] = Field(
        default_factory=list,
        description="Recent query log entries (max 5, most recent first)"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }
