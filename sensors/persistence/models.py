"""State models for persistence layer."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ScoreInfo(BaseModel):
    """Numerical score for trend comparison between states."""

    value: int = Field(
        description="Numerical score (e.g. number of failed tests, linting issues)"
    )
    direction: Literal["more", "less"] = Field(
        description="Which direction is better: 'less' means lower is better, 'more' means higher is better"
    )
    description: str = Field(
        default="",
        description="Human-readable description of what the score measures (e.g. 'Number of failing tests')"
    )


class RunnerResult(BaseModel):
    """Result from a single runner execution."""

    timestamp: datetime = Field(
        description="When this result was generated"
    )
    success: bool = Field(
        description="Whether the runner executed successfully"
    )
    output: dict[str, Any] = Field(
        default_factory=dict,
        description="Runner-specific output data (violations, test results, etc.)"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class FormattedOutput(BaseModel):
    """Pre-computed formatted strings for each client type.

    Populated by the output parser at write time so consumers
    never need to load parsers.
    """

    details_terminal: str = Field(
        default="",
        description="Short summary with Rich markup for terminal display"
    )
    details_html: str = Field(
        default="",
        description="Short summary as HTML for web dashboards"
    )
    details_llm: str = Field(
        default="",
        description="Short summary as plain text for LLM/agent consumption"
    )
    failures_terminal: str = Field(
        default="",
        description="Multi-line failure details with Rich markup"
    )
    failures_html: str = Field(
        default="",
        description="Multi-line failure details as HTML"
    )
    failures_llm: str = Field(
        default="",
        description="Multi-line failure details as plain text for LLM/agent"
    )


class RunnerState(BaseModel):
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
    formatted: FormattedOutput = Field(
        default_factory=FormattedOutput,
        description="Pre-computed formatted output for each client type"
    )
    score: ScoreInfo | None = Field(
        default=None,
        description="Numerical score for trend comparison"
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


class Snapshot(BaseModel):
    """A point-in-time snapshot of all runner states for comparison."""

    snapshot_id: str = Field(
        description="Unique identifier for this snapshot, used to group history records"
    )
    timestamp: datetime = Field(
        description="When the snapshot was taken"
    )
    runners: dict[str, RunnerState] = Field(
        default_factory=dict,
        description="Runner states at snapshot time"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class RunnerCheckSummary(BaseModel):
    """Snapshot of a single runner's state at check time."""

    status: Literal["success", "failure", "below_threshold"] = Field(
        description="Runner status at check time"
    )
    score: ScoreInfo | None = Field(
        default=None,
        description="Score at check time, if available"
    )


class CheckHistoryEntry(BaseModel):
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
    runners: dict[str, RunnerCheckSummary] = Field(
        default_factory=dict,
        description="Per-runner status and score at check time"
    )

    class Config:
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class SensorsState(BaseModel):
    """Overall sensors state containing all runners."""

    lastUpdated: datetime = Field(
        description="When the sensors state was last updated"
    )
    runners: dict[str, RunnerState] = Field(
        default_factory=dict,
        description="State for each runner, keyed by runner name"
    )
    snapshot: Snapshot | None = Field(
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
